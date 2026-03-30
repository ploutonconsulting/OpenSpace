#!/usr/bin/env python3
"""
Sync the OpenSpace dashboard database with the skills on disk.

Deactivates DB records for skills no longer on disk, and triggers
import of new skills found on disk. Designed to run periodically
(e.g. via cron) alongside the WAL checkpoint.

Usage:
  python3 sync_dashboard_skills.py --db <path-to-openspace.db> --skills-dir <path>
  python3 sync_dashboard_skills.py --db ~/.openspace/openspace.db --skills-dir ~/.claude/skills

Output:
  Prints a summary of changes made (deactivated, imported, unchanged).
  Exits 0 on success, 1 on error.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def get_db_skills(conn: sqlite3.Connection) -> dict:
    """Return dict of {name: (skill_id, is_active, path)} from DB."""
    rows = conn.execute(
        "SELECT skill_id, name, is_active, path FROM skill_records"
    ).fetchall()
    return {row[1]: {"skill_id": row[0], "is_active": row[2], "path": row[3]} for row in rows}


def get_disk_skills(skills_dir: Path) -> set:
    """Return set of skill names present on disk (dirs with SKILL.md)."""
    skills = set()
    if not skills_dir.is_dir():
        return skills
    for child in skills_dir.iterdir():
        if child.is_dir() and (child / "SKILL.md").exists():
            skills.add(child.name)
    return skills


def deactivate_removed(conn: sqlite3.Connection, db_skills: dict, disk_skills: set) -> list:
    """Mark skills as inactive if they no longer exist on disk."""
    deactivated = []
    for name, info in db_skills.items():
        if name not in disk_skills and info["is_active"] == 1:
            conn.execute(
                "UPDATE skill_records SET is_active = 0, last_updated = ? WHERE skill_id = ?",
                (datetime.now(timezone.utc).isoformat(), info["skill_id"]),
            )
            deactivated.append(name)
    return deactivated


def import_new_skills(conn: sqlite3.Connection, db_skills: dict, disk_skills: set, skills_dir: Path) -> list:
    """Create DB records for skills on disk but not in DB."""
    imported = []
    for name in sorted(disk_skills - set(db_skills.keys())):
        skill_path = skills_dir / name / "SKILL.md"
        if not skill_path.exists():
            continue

        # Parse frontmatter for description
        text = skill_path.read_text(encoding="utf-8")
        description = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                frontmatter = text[3:end]
                for line in frontmatter.splitlines():
                    if line.strip().startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"').strip("'")
                        # Handle multi-line description (take first line)
                        if description.startswith(">"):
                            description = ""
                            # Collect continuation lines
                            in_desc = True
                            for fl in frontmatter.splitlines():
                                if fl.strip().startswith("description:"):
                                    in_desc = True
                                    continue
                                elif in_desc and fl.startswith("  "):
                                    description += " " + fl.strip()
                                elif in_desc and not fl.startswith("  "):
                                    break
                            description = description.strip()
                        break

        now = datetime.now(timezone.utc).isoformat()
        # Use the .skill_id sidecar if it exists (matches MCP server)
        id_file = skills_dir / name / ".skill_id"
        if id_file.exists():
            try:
                skill_id = id_file.read_text(encoding="utf-8").strip()
            except OSError:
                skill_id = f"{name}__imp_{hash(name) & 0xFFFFFFFF:08x}"
        else:
            skill_id = f"{name}__imp_{hash(name) & 0xFFFFFFFF:08x}"

        conn.execute(
            """INSERT INTO skill_records
               (skill_id, name, description, path, is_active,
                lineage_origin, lineage_generation,
                lineage_content_snapshot, lineage_created_at,
                first_seen, last_updated)
               VALUES (?, ?, ?, ?, 1, 'imported', 0, '{}', ?, ?, ?)""",
            (skill_id, name, description, str(skill_path), now, now, now),
        )
        imported.append(name)

    return imported


def reactivate_returned(conn: sqlite3.Connection, db_skills: dict, disk_skills: set) -> list:
    """Reactivate skills that were previously deactivated but are back on disk."""
    reactivated = []
    for name, info in db_skills.items():
        if name in disk_skills and info["is_active"] == 0:
            conn.execute(
                "UPDATE skill_records SET is_active = 1, last_updated = ? WHERE skill_id = ?",
                (datetime.now(timezone.utc).isoformat(), info["skill_id"]),
            )
            reactivated.append(name)
    return reactivated


def align_skill_ids(conn: sqlite3.Connection, db_skills: dict, skills_dir: Path) -> list:
    """Align DB skill_ids with the .skill_id sidecar files on disk.

    The MCP server reads .skill_id files to determine IDs. If the dashboard
    DB was imported separately, its IDs won't match. This migrates DB records
    to use the authoritative disk IDs so execution stats flow through correctly.
    """
    aligned = []
    for name, info in db_skills.items():
        id_file = skills_dir / name / ".skill_id"
        if not id_file.exists():
            continue

        try:
            disk_id = id_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        if not disk_id or disk_id == info["skill_id"]:
            continue

        old_id = info["skill_id"]

        # Check if the disk_id already exists in DB (avoid PK conflict)
        existing = conn.execute(
            "SELECT skill_id FROM skill_records WHERE skill_id = ?", (disk_id,)
        ).fetchone()

        if existing:
            # Merge: transfer stats from old record to existing, then delete old
            conn.execute(
                """UPDATE skill_records SET
                     total_selections = total_selections + (SELECT total_selections FROM skill_records WHERE skill_id = ?),
                     total_applied = total_applied + (SELECT total_applied FROM skill_records WHERE skill_id = ?),
                     total_completions = total_completions + (SELECT total_completions FROM skill_records WHERE skill_id = ?),
                     total_fallbacks = total_fallbacks + (SELECT total_fallbacks FROM skill_records WHERE skill_id = ?),
                     last_updated = ?
                   WHERE skill_id = ?""",
                (old_id, old_id, old_id, old_id,
                 datetime.now(timezone.utc).isoformat(), disk_id),
            )
            conn.execute("DELETE FROM skill_records WHERE skill_id = ?", (old_id,))
        else:
            # Simple rename: update the PK
            conn.execute(
                "UPDATE skill_records SET skill_id = ?, last_updated = ? WHERE skill_id = ?",
                (disk_id, datetime.now(timezone.utc).isoformat(), old_id),
            )

        # Migrate related tables
        for table, col in [
            ("skill_judgments", "skill_id"),
            ("skill_lineage_parents", "skill_id"),
            ("skill_tags", "skill_id"),
            ("skill_tool_deps", "skill_id"),
        ]:
            try:
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                    (disk_id, old_id),
                )
            except sqlite3.OperationalError:
                pass  # table may not exist

        aligned.append(f"{name}: {old_id} → {disk_id}")

    return aligned


def main():
    parser = argparse.ArgumentParser(description="Sync OpenSpace dashboard DB with skills on disk")
    parser.add_argument("--db", type=str, required=True, help="Path to openspace.db")
    parser.add_argument("--skills-dir", type=str, required=True, help="Path to skills directory")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    db_path = Path(args.db)
    skills_dir = Path(args.skills_dir)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    if not skills_dir.is_dir():
        print(f"ERROR: Skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        db_skills = get_db_skills(conn)
        disk_skills = get_disk_skills(skills_dir)

        aligned = align_skill_ids(conn, db_skills, skills_dir)
        # Re-read after alignment since IDs may have changed
        if aligned:
            db_skills = get_db_skills(conn)

        deactivated = deactivate_removed(conn, db_skills, disk_skills)
        reactivated = reactivate_returned(conn, db_skills, disk_skills)
        imported = import_new_skills(conn, db_skills, disk_skills, skills_dir)

        if args.dry_run:
            print("DRY RUN — no changes applied")
            conn.rollback()
        else:
            conn.commit()
            # Checkpoint WAL while we're here
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        print(f"Skills on disk:  {len(disk_skills)}")
        print(f"Skills in DB:    {len(db_skills)}")
        if aligned:
            print(f"IDs aligned:     {len(aligned)}")
            for a in aligned:
                print(f"  {a}")
        if deactivated:
            print(f"Deactivated:     {', '.join(deactivated)}")
        if reactivated:
            print(f"Reactivated:     {', '.join(reactivated)}")
        if imported:
            print(f"Imported:        {', '.join(imported)}")
        if not aligned and not deactivated and not reactivated and not imported:
            print("No changes needed — DB is in sync")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
