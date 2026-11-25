"""Memory DB (topic deduplication) and local reusable SFX + BGM cache."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from src.brand_config import get_disciplines


class StudioMemory:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "data" / "memory.db"
        self.sfx_dir = self.base_dir / "data" / "sfx_cache"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.sync_idea_bank_from_json()
        self.ensure_sfx_cache()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    phenomenon TEXT NOT NULL,
                    discipline TEXT DEFAULT 'gadgets',
                    status TEXT NOT NULL,
                    final_score REAL,
                    gdrive_url TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)").fetchall()}
            if "discipline" not in cols:
                conn.execute("ALTER TABLE episodes ADD COLUMN discipline TEXT DEFAULT 'gadgets'")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idea_bank (
                    id TEXT PRIMARY KEY,
                    discipline TEXT NOT NULL,
                    title TEXT NOT NULL,
                    phenomenon TEXT NOT NULL,
                    hook_3s TEXT NOT NULL,
                    real_footage_keywords TEXT NOT NULL,
                    used_by_episode TEXT DEFAULT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episode_analytics (
                    episode_id TEXT PRIMARY KEY,
                    published_at_wib TEXT,
                    section TEXT DEFAULT 'EVERYDAY_TECH',
                    title TEXT NOT NULL,
                    discipline TEXT DEFAULT '-',
                    youtube_url TEXT,
                    gdrive_folder_url TEXT,
                    qa_score REAL,
                    duration_sec REAL,
                    production_seconds REAL,
                    revisions_used INTEGER,
                    topic_attempts_used INTEGER,
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    shares INTEGER,
                    avg_view_percentage REAL,
                    avg_view_duration_sec REAL,
                    subscribers_gained INTEGER,
                    agent_models TEXT,
                    last_audited_week TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_snapshots (
                    week_ending TEXT PRIMARY KEY,
                    subscribers INTEGER DEFAULT 0,
                    total_views INTEGER DEFAULT 0,
                    video_count_audited INTEGER DEFAULT 0,
                    avg_qa REAL,
                    avg_retention_pct REAL,
                    radar_avg_views REAL,
                    everyday_avg_views REAL,
                    top_countries_json TEXT,
                    geo_insights TEXT,
                    radar_directives_json TEXT,
                    summary TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_clip_usage (
                    file_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    episode_id TEXT,
                    used_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def get_used_flow_clip_ids(self) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT file_id FROM flow_clip_usage").fetchall()
            return {str(r[0]) for r in rows if r and r[0]}

    def mark_flow_clip_used(self, file_id: str, filename: str, episode_id: str = "") -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO flow_clip_usage (file_id, filename, episode_id, used_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(file_id), str(filename), str(episode_id or ""), now_iso),
            )
            conn.commit()

    def sync_idea_bank_from_json(self) -> int:
        bank_json = self.base_dir / "data" / "idea_bank.json"
        if not bank_json.exists():
            return 0
        payload = json.loads(bank_json.read_text(encoding="utf-8"))
        ideas = payload.get("ideas", [])
        with sqlite3.connect(self.db_path) as conn:
            for item in ideas:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO idea_bank
                    (id, discipline, title, phenomenon, hook_3s, real_footage_keywords)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["discipline"],
                        item["title"],
                        item["phenomenon"],
                        item["hook_3s"],
                        json.dumps(item.get("real_footage_keywords", []), ensure_ascii=False),
                    ),
                )
            conn.commit()
        return len(ideas)

    def mark_idea_used(self, idea_id: str, episode_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE idea_bank SET used_by_episode = ? WHERE id = ?",
                (episode_id, idea_id),
            )
            conn.commit()

    def discipline_counts(self) -> dict[str, int]:
        counts = {d: 0 for d in get_disciplines()}
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT discipline, COUNT(*) FROM episodes GROUP BY discipline"
            ).fetchall()
        for d, cnt in rows:
            if d in counts:
                counts[d] = cnt
        return counts

    def get_balanced_unused_ideas(self, count: int = 5) -> list[dict]:
        """Pick unused ideas from idea_bank prioritizing underrepresented everyday sci-tech pillars."""
        counts = self.discipline_counts()
        ordered_disciplines = sorted(counts.keys(), key=lambda d: counts[d])
        picked: list[dict] = []
        picked_ids: set[str] = set()

        with sqlite3.connect(self.db_path) as conn:
            for disc in ordered_disciplines:
                if len(picked) >= count:
                    break
                row = conn.execute(
                    """
                    SELECT id, discipline, title, phenomenon, hook_3s, real_footage_keywords
                    FROM idea_bank
                    WHERE used_by_episode IS NULL AND discipline = ?
                    ORDER BY id ASC LIMIT 1
                    """,
                    (disc,),
                ).fetchone()
                if row and row[0] not in picked_ids:
                    picked_ids.add(row[0])
                    picked.append(
                        {
                            "id": row[0],
                            "discipline": row[1],
                            "title": row[2],
                            "phenomenon": row[3],
                            "hook_3s": row[4],
                            "real_footage_keywords": json.loads(row[5]),
                        }
                    )
            if len(picked) < count:
                rows = conn.execute(
                    """
                    SELECT id, discipline, title, phenomenon, hook_3s, real_footage_keywords
                    FROM idea_bank
                    WHERE used_by_episode IS NULL
                    ORDER BY id ASC
                    """
                ).fetchall()
                for row in rows:
                    if len(picked) >= count:
                        break
                    if row[0] not in picked_ids:
                        picked_ids.add(row[0])
                        picked.append(
                            {
                                "id": row[0],
                                "discipline": row[1],
                                "title": row[2],
                                "phenomenon": row[3],
                                "hook_3s": row[4],
                                "real_footage_keywords": json.loads(row[5]),
                            }
                        )
            # Recycle ideas gracefully if entire bank has been used
            if len(picked) < count:
                rows = conn.execute(
                    """
                    SELECT id, discipline, title, phenomenon, hook_3s, real_footage_keywords
                    FROM idea_bank
                    ORDER BY RANDOM() LIMIT ?
                    """,
                    (count - len(picked),),
                ).fetchall()
                for row in rows:
                    if row[0] not in picked_ids:
                        picked_ids.add(row[0])
                        picked.append(
                            {
                                "id": row[0],
                                "discipline": row[1],
                                "title": row[2],
                                "phenomenon": row[3],
                                "hook_3s": row[4],
                                "real_footage_keywords": json.loads(row[5]),
                            }
                        )
        return picked

    def list_past_topics(self, limit: int = 30) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT slug, title, phenomenon, discipline, status FROM episodes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"slug": r[0], "title": r[1], "phenomenon": r[2], "discipline": r[3], "status": r[4]}
            for r in rows
        ]

    def record_episode(
        self,
        episode_id: str,
        slug: str,
        title: str,
        phenomenon: str,
        status: str,
        discipline: str = "gadgets",
        final_score: Optional[float] = None,
        gdrive_url: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes
                (episode_id, slug, title, phenomenon, discipline, status, final_score, gdrive_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (episode_id, slug, title, phenomenon, discipline, status, final_score, gdrive_url, now),
            )
            conn.commit()

    def upsert_episode_analytics(
        self,
        rows: list[dict],
        week_ending: Optional[str] = None,
    ) -> int:
        if not rows:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        with sqlite3.connect(self.db_path) as conn:
            existing_by_yt: dict[str, str] = {}
            for eid, yurl in conn.execute("SELECT episode_id, youtube_url FROM episode_analytics").fetchall():
                m = re.search(r"(?:/shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", yurl or "")
                if m:
                    existing_by_yt[m.group(1)] = eid

            for r in rows:
                yurl = r.get("youtube_url") or ""
                vid = r.get("video_id")
                if not vid and yurl:
                    m = re.search(r"(?:/shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", yurl)
                    vid = m.group(1) if m else None
                ep_id = (r.get("episode_id") or "").strip()
                if not ep_id and vid:
                    ep_id = existing_by_yt.get(vid, f"yt_{vid}")
                if not ep_id:
                    continue
                if vid:
                    existing_by_yt[vid] = ep_id

                title = str(r.get("title") or ep_id).strip()
                published_at = r.get("published_at_wib") or r.get("published_at") or ""
                section_raw = str(r.get("section") or "").upper()
                if "RADAR" in section_raw or "WEEKEND" in section_raw or "radar" in ep_id.lower():
                    section = "TECH_RADAR"
                else:
                    section = "EVERYDAY_TECH"

                prev = conn.execute(
                    "SELECT published_at_wib, section, discipline, youtube_url, gdrive_folder_url, qa_score, duration_sec, production_seconds, revisions_used, topic_attempts_used, agent_models FROM episode_analytics WHERE episode_id = ?",
                    (ep_id,),
                ).fetchone()

                disc = r.get("discipline") or (prev[2] if prev else "-") or "-"
                yurl_final = yurl or (prev[3] if prev else None)
                gurl_final = r.get("gdrive_folder_url") or (prev[4] if prev else None)
                qa_final = r.get("qa_score") if r.get("qa_score") is not None else (prev[5] if prev else None)
                dur_final = r.get("duration_sec") if r.get("duration_sec") is not None else (prev[6] if prev else None)
                prod_final = r.get("production_seconds") if r.get("production_seconds") is not None else (prev[7] if prev else None)
                rev_final = r.get("revisions_used") if r.get("revisions_used") is not None else (prev[8] if prev else None)
                att_final = r.get("topic_attempts_used") if r.get("topic_attempts_used") is not None else (prev[9] if prev else None)
                models_raw = r.get("agent_models")
                if isinstance(models_raw, dict) and models_raw:
                    models_str = " | ".join(f"{k}: {v}" for k, v in models_raw.items())
                elif isinstance(models_raw, str) and models_raw:
                    models_str = models_raw
                else:
                    models_str = prev[10] if prev else None

                conn.execute(
                    """
                    INSERT INTO episode_analytics (
                        episode_id, published_at_wib, section, title, discipline,
                        youtube_url, gdrive_folder_url, qa_score, duration_sec,
                        production_seconds, revisions_used, topic_attempts_used,
                        views, likes, comments, shares, avg_view_percentage,
                        avg_view_duration_sec, subscribers_gained, agent_models,
                        last_audited_week, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(episode_id) DO UPDATE SET
                        published_at_wib = COALESCE(NULLIF(excluded.published_at_wib, ''), episode_analytics.published_at_wib),
                        section = excluded.section,
                        title = excluded.title,
                        discipline = COALESCE(NULLIF(excluded.discipline, '-'), episode_analytics.discipline),
                        youtube_url = COALESCE(excluded.youtube_url, episode_analytics.youtube_url),
                        gdrive_folder_url = COALESCE(excluded.gdrive_folder_url, episode_analytics.gdrive_folder_url),
                        qa_score = COALESCE(excluded.qa_score, episode_analytics.qa_score),
                        duration_sec = COALESCE(excluded.duration_sec, episode_analytics.duration_sec),
                        production_seconds = COALESCE(excluded.production_seconds, episode_analytics.production_seconds),
                        revisions_used = COALESCE(excluded.revisions_used, episode_analytics.revisions_used),
                        topic_attempts_used = COALESCE(excluded.topic_attempts_used, episode_analytics.topic_attempts_used),
                        views = MAX(COALESCE(episode_analytics.views, 0), COALESCE(excluded.views, 0)),
                        likes = MAX(COALESCE(episode_analytics.likes, 0), COALESCE(excluded.likes, 0)),
                        comments = MAX(COALESCE(episode_analytics.comments, 0), COALESCE(excluded.comments, 0)),
                        shares = COALESCE(excluded.shares, episode_analytics.shares),
                        avg_view_percentage = COALESCE(excluded.avg_view_percentage, episode_analytics.avg_view_percentage),
                        avg_view_duration_sec = COALESCE(excluded.avg_view_duration_sec, episode_analytics.avg_view_duration_sec),
                        subscribers_gained = COALESCE(excluded.subscribers_gained, episode_analytics.subscribers_gained),
                        agent_models = COALESCE(excluded.agent_models, episode_analytics.agent_models),
                        last_audited_week = COALESCE(excluded.last_audited_week, episode_analytics.last_audited_week),
                        updated_at = excluded.updated_at
                    """,
                    (
                        ep_id,
                        published_at or (prev[0] if prev else ""),
                        section,
                        title,
                        disc,
                        yurl_final,
                        gurl_final,
                        qa_final,
                        dur_final,
                        prod_final,
                        rev_final,
                        att_final,
                        int(r.get("views") or 0),
                        int(r.get("likes") or 0),
                        int(r.get("comments") or 0),
                        r.get("shares"),
                        r.get("avg_view_percentage"),
                        r.get("avg_view_duration_sec"),
                        r.get("subscribers_gained"),
                        models_str,
                        week_ending,
                        now,
                    ),
                )
                count += 1
            conn.commit()
        return count

    def upsert_weekly_snapshot(self, snapshot: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO weekly_snapshots (
                    week_ending, subscribers, total_views, video_count_audited,
                    avg_qa, avg_retention_pct, radar_avg_views, everyday_avg_views,
                    top_countries_json, geo_insights, radar_directives_json, summary, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(week_ending) DO UPDATE SET
                    subscribers = excluded.subscribers,
                    total_views = excluded.total_views,
                    video_count_audited = excluded.video_count_audited,
                    avg_qa = excluded.avg_qa,
                    avg_retention_pct = excluded.avg_retention_pct,
                    radar_avg_views = excluded.radar_avg_views,
                    everyday_avg_views = excluded.everyday_avg_views,
                    top_countries_json = excluded.top_countries_json,
                    geo_insights = excluded.geo_insights,
                    radar_directives_json = excluded.radar_directives_json,
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot["week_ending"],
                    int(snapshot.get("subscribers") or 0),
                    int(snapshot.get("total_views") or 0),
                    int(snapshot.get("video_count_audited") or 0),
                    snapshot.get("avg_qa"),
                    snapshot.get("avg_retention_pct"),
                    snapshot.get("radar_avg_views"),
                    snapshot.get("everyday_avg_views"),
                    json.dumps(snapshot.get("top_countries") or [], ensure_ascii=False),
                    snapshot.get("geo_insights") or "",
                    json.dumps(snapshot.get("radar_directives") or [], ensure_ascii=False),
                    snapshot.get("summary") or "",
                    now,
                ),
            )
            conn.commit()

    def get_milestone_summary(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total_videos = conn.execute("SELECT COUNT(*) FROM episode_analytics").fetchone()[0]
            total_weeks = conn.execute("SELECT COUNT(*) FROM weekly_snapshots").fetchone()[0]
            radar_row = conn.execute(
                "SELECT COUNT(*), AVG(views), AVG(avg_view_percentage) FROM episode_analytics WHERE section = 'TECH_RADAR'"
            ).fetchone()
            reg_row = conn.execute(
                "SELECT COUNT(*), AVG(views), AVG(avg_view_percentage) FROM episode_analytics WHERE section != 'TECH_RADAR'"
            ).fetchone()
            best_disc_row = conn.execute(
                "SELECT discipline, COUNT(*), AVG(views) FROM episode_analytics WHERE discipline IS NOT NULL AND discipline != '-' GROUP BY discipline ORDER BY AVG(views) DESC LIMIT 1"
            ).fetchone()
            latest_week = conn.execute(
                "SELECT week_ending, subscribers, total_views FROM weekly_snapshots ORDER BY week_ending DESC LIMIT 1"
            ).fetchone()

        return {
            "total_videos_archived": int(total_videos or 0),
            "total_weeks_audited": int(total_weeks or 0),
            "radar_videos": int(radar_row[0] or 0),
            "everyday_videos": int(reg_row[0] or 0),
            "radar_avg_views": round(float(radar_row[1]), 1) if radar_row[1] is not None else None,
            "everyday_avg_views": round(float(reg_row[1]), 1) if reg_row[1] is not None else None,
            "radar_avg_retention_pct": round(float(radar_row[2]), 1) if radar_row[2] is not None else None,
            "everyday_avg_retention_pct": round(float(reg_row[2]), 1) if reg_row[2] is not None else None,
            "top_discipline": best_disc_row[0] if best_disc_row else "-",
            "top_discipline_avg_views": round(float(best_disc_row[2]), 1) if best_disc_row and best_disc_row[2] is not None else 0.0,
            "latest_week": latest_week[0] if latest_week else "-",
        }

    def get_all_episode_analytics(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT episode_id, published_at_wib, section, title, discipline,
                       youtube_url, gdrive_folder_url, qa_score, duration_sec,
                       views, likes, comments, shares, avg_view_percentage,
                       avg_view_duration_sec, subscribers_gained, last_audited_week
                FROM episode_analytics
                ORDER BY COALESCE(published_at_wib, updated_at) DESC, episode_id DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def export_master_csv(self, csv_path: Path) -> int:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            "Record_Type",
            "Published_Date_WIB",
            "Audit_Week",
            "Episode_ID",
            "Section",
            "Title_Or_Report",
            "Pillar",
            "YouTube_URL",
            "Drive_URL",
            "Duration_Sec",
            "QA_Score",
            "Views",
            "Likes",
            "Comments",
            "Shares",
            "Retention_Pct",
            "Avg_View_Duration_Sec",
            "Subs_Gained",
            "Channel_Subscribers",
            "Channel_Total_Views",
            "Top_Countries",
            "Production_Sec",
            "Revisions",
            "AI_Models_Used",
            "Geo_Insights_And_Summary",
        ]
        with sqlite3.connect(self.db_path) as conn:
            weeks = conn.execute(
                """
                SELECT week_ending, subscribers, total_views, video_count_audited, avg_qa,
                       avg_retention_pct, radar_avg_views, everyday_avg_views,
                       top_countries_json, geo_insights, summary
                FROM weekly_snapshots
                ORDER BY week_ending DESC
                """
            ).fetchall()
            eps = conn.execute(
                """
                SELECT episode_id, published_at_wib, section, title, discipline,
                       youtube_url, gdrive_folder_url, qa_score, duration_sec,
                       production_seconds, revisions_used, views, likes, comments,
                       shares, avg_view_percentage, avg_view_duration_sec,
                       subscribers_gained, agent_models, last_audited_week
                FROM episode_analytics
                ORDER BY COALESCE(published_at_wib, updated_at) DESC, episode_id DESC
                """
            ).fetchall()

        total_rows = 0
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for wk in weeks:
                (
                    w_end, subs, tot_v, v_cnt, avg_qa, avg_ret, r_avg, e_avg,
                    top_c_json, geo_ins, summary_txt
                ) = wk
                try:
                    c_list = json.loads(top_c_json or "[]")
                    c_str = ", ".join(f"{c.get('country')}:{c.get('views')}" for c in c_list[:5]) or "-"
                except Exception:
                    c_str = "-"
                cmp_txt = f"TECH_RADAR avg {r_avg if r_avg is not None else '-'} vs EVERYDAY_TECH avg {e_avg if e_avg is not None else '-'}"
                note = f"{cmp_txt} | {geo_ins or '-'} | {summary_txt or '-'}"
                writer.writerow([
                    "WEEKLY_AUDIT",
                    w_end,
                    w_end,
                    f"audit_{w_end}",
                    "MILESTONE",
                    f"Weekly Audit {w_end} ({v_cnt} videos)",
                    "-",
                    "-",
                    "-",
                    "-",
                    avg_qa if avg_qa is not None else "-",
                    tot_v,
                    "-",
                    "-",
                    "-",
                    avg_ret if avg_ret is not None else "-",
                    "-",
                    "-",
                    subs,
                    tot_v,
                    c_str,
                    "-",
                    "-",
                    "-",
                    note[:1200],
                ])
                total_rows += 1

            for ep in eps:
                (
                    ep_id, pub_at, sec, title, disc, yurl, gurl, qa, dur,
                    prod_s, rev, views, likes, cmts, shares, ret, avg_t,
                    subs_g, models, last_wk
                ) = ep
                writer.writerow([
                    "VIDEO",
                    pub_at or "-",
                    last_wk or "-",
                    ep_id,
                    sec or "EVERYDAY_TECH",
                    title,
                    disc or "-",
                    yurl or "-",
                    gurl or "-",
                    dur if dur is not None else "-",
                    qa if qa is not None else "-",
                    views if views is not None else 0,
                    likes if likes is not None else 0,
                    cmts if cmts is not None else 0,
                    shares if shares is not None else "-",
                    ret if ret is not None else "-",
                    avg_t if avg_t is not None else "-",
                    subs_g if subs_g is not None else "-",
                    "-",
                    "-",
                    "-",
                    prod_s if prod_s is not None else "-",
                    rev if rev is not None else "-",
                    models or "-",
                    "-",
                ])
                total_rows += 1
        return total_rows

    def get_sfx_path(self, cue: Optional[str]) -> Optional[str]:
        if not cue or cue == "none":
            return None
        path = self.sfx_dir / f"{cue}.wav"
        return str(path) if path.exists() else None

    def get_bgm_path(self) -> str:
        path = self.sfx_dir / "bgm_curious_science.wav"
        if not path.exists():
            self.ensure_sfx_cache()
        return str(path)

    def ensure_sfx_cache(self) -> None:
        """Synthesize clean, royalty-free reusable SFX & subtle tech-curiosity BGM bed once."""
        sr = 44100

        def save_wav(path: Path, samples: np.ndarray) -> None:
            if path.exists():
                return
            peak = float(np.max(np.abs(samples))) if len(samples) else 1.0
            if peak > 0.95:
                samples = samples / peak * 0.92
            pcm = (samples * 32767).astype(np.int16)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(pcm.tobytes())

        t = np.linspace(0, 0.42, int(sr * 0.42), endpoint=False)
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 1, len(t))
        kernel = np.hanning(35)
        kernel /= kernel.sum()
        filtered = np.convolve(noise, kernel, mode="same")
        env = np.sin(np.pi * t / t[-1]) ** 1.6
        tone = 0.25 * np.sin(2 * np.pi * (220 + 520 * (t / t[-1])) * t)
        save_wav(self.sfx_dir / "whoosh.wav", (filtered * 0.75 + tone) * env * 0.85)

        t = np.linspace(0, 0.16, int(sr * 0.16), endpoint=False)
        freq = 320 + 680 * np.exp(-t * 28)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        env = np.exp(-t * 26) * np.minimum(1.0, t / 0.006)
        save_wav(self.sfx_dir / "pop.wav", np.sin(phase) * env * 0.85)

        t = np.linspace(0, 0.65, int(sr * 0.65), endpoint=False)
        sig = (
            0.65 * np.sin(2 * np.pi * 1046.5 * t)
            + 0.25 * np.sin(2 * np.pi * 2093.0 * t)
            + 0.12 * np.sin(2 * np.pi * 3136.0 * t)
        )
        env = np.exp(-t * 5.5) * np.minimum(1.0, t / 0.008)
        save_wav(self.sfx_dir / "ding.wav", sig * env * 0.8)

        t = np.linspace(0, 0.75, int(sr * 0.75), endpoint=False)
        n1 = np.sin(2 * np.pi * 523.25 * t) * np.exp(-t * 4.5)
        t2 = np.maximum(0.0, t - 0.14)
        mask2 = (t >= 0.14).astype(float)
        n2 = mask2 * np.sin(2 * np.pi * 659.25 * t2) * np.exp(-t2 * 4.5)
        n3 = mask2 * 0.4 * np.sin(2 * np.pi * 783.99 * t2) * np.exp(-t2 * 5.0)
        save_wav(self.sfx_dir / "chime.wav", (n1 * 0.55 + n2 * 0.6 + n3 * 0.4) * 0.8)

        t = np.linspace(0, 0.75, int(sr * 0.75), endpoint=False)
        freq = 180 + 420 * (t / t[-1]) ** 2
        phase = 2 * np.pi * np.cumsum(freq) / sr
        env = (t / t[-1]) ** 1.8 * np.minimum(1.0, (t[-1] - t) / 0.05)
        save_wav(self.sfx_dir / "riser.wav", np.sin(phase) * env * 0.65)

        t = np.linspace(0, 0.30, int(sr * 0.30), endpoint=False)
        s1 = np.sin(2 * np.pi * 880.0 * t) * np.exp(-t * 14.0)
        ts = np.maximum(0.0, t - 0.06)
        ms = (t >= 0.06).astype(float)
        s2 = ms * np.sin(2 * np.pi * 1318.5 * ts) * np.exp(-ts * 12.0)
        save_wav(self.sfx_dir / "spark.wav", (s1 * 0.6 + s2 * 0.75) * 0.82)

        bgm_path = self.sfx_dir / "bgm_curious_science.wav"
        if not bgm_path.exists():
            dur = 16.0
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            chords = [
                (261.63, 329.63, 392.00),
                (220.00, 261.63, 329.63),
                (174.61, 220.00, 261.63),
                (196.00, 246.94, 293.66),
            ]
            pad = np.zeros_like(t)
            pluck = np.zeros_like(t)
            for idx, chord in enumerate(chords):
                c_start = idx * 4.0
                c_end = (idx + 1) * 4.0
                mask = (t >= c_start) & (t < c_end)
                lt = t[mask] - c_start
                c_env = np.sin(np.pi * lt / 4.0) ** 0.4
                for f in chord:
                    pad[mask] += 0.14 * np.sin(2 * np.pi * f * lt) * c_env
                notes = [chord[0] * 2, chord[1] * 2, chord[2] * 2, chord[1] * 2] * 2
                for step, nf in enumerate(notes):
                    s_start = c_start + step * 0.5
                    s_end = min(dur, s_start + 0.45)
                    smask = (t >= s_start) & (t < s_end)
                    st = t[smask] - s_start
                    senv = np.exp(-st * 9.5) * np.minimum(1.0, st / 0.008)
                    pluck[smask] += 0.22 * (
                        np.sin(2 * np.pi * nf * st) + 0.25 * np.sin(2 * np.pi * nf * 2 * st)
                    ) * senv
            mix = (pad * 0.55 + pluck * 0.45) * 0.65
            save_wav(bgm_path, mix)
