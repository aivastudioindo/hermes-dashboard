#!/usr/bin/env python3
"""Hermes Profile Skill & Memory Dashboard - lightweight stdlib server.

Serves a single-page HTML dashboard that lists, edits, deletes, and
toggles (enable/disable) skills and memory files for each Hermes profile.

No external dependencies - uses only the Python standard library so it
runs on Termux/ARM without npm/build steps.

Endpoints:
  GET  /                     -> dashboard HTML
  GET  /api/profiles         -> list of profile names
  GET  /api/data?profile=NAME -> skills + memories for a profile
  GET  /api/raw?profile=NAME&kind=skill|memory&name=... -> raw file content
  POST /api/save  json {profile,kind,name,content,new_name?} -> write file
  POST /api/delete json {profile,kind,name} -> remove
  POST /api/toggle json {profile,skill} -> add/remove from skills.disabled
"""
import json
import os
import subprocess
import re
import shutil
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERMES_HOME = os.path.expanduser("~/.hermes")
PORT = int(os.environ.get("HERMES_DASHBOARD_PORT", "8765"))

# Profiles: default maps to HERMES_HOME root; others to profiles/<name>
def profile_home(name: str) -> str:
    if name == "default":
        return HERMES_HOME
    return os.path.join(HERMES_HOME, "profiles", name)

def list_profiles() -> list:
    profs = ["default"]
    pdir = os.path.join(HERMES_HOME, "profiles")
    if os.path.isdir(pdir):
        for d in sorted(os.listdir(pdir)):
            if os.path.isdir(os.path.join(pdir, d)):
                profs.append(d)
    return profs

def read_manifest(home: str) -> dict:
    """Return dict name->hash for bundled (built-in) skills."""
    mf = os.path.join(home, "skills", ".bundled_manifest")
    out = {}
    if os.path.isfile(mf):
        for line in open(mf):
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out

def read_disabled(home: str) -> list:
    """Parse skills.disabled from config.yaml (naive, flat list under 'skills:')."""
    cfg = os.path.join(home, "config.yaml")
    disabled = []
    if not os.path.isfile(cfg):
        return disabled
    txt = open(cfg).read()
    m = re.search(r"^skills:\s*\n((?:[ \t]+.*\n?)*)", txt, re.MULTILINE)
    if not m:
        return disabled
    block = m.group(1)
    dm = re.search(r"disabled:\s*\n((?:[ \t]+-.*\n?)*)", block)
    if dm:
        for ln in dm.group(1).splitlines():
            ln = ln.strip()
            if ln.startswith("-"):
                disabled.append(ln[1:].strip())
    return disabled

def write_disabled(home: str, disabled: list) -> None:
    """Rewrite ONLY the skills.disabled list in config.yaml, preserving everything else.

    Strategy: find the 'skills:' block. Inside it, replace the 'disabled:' list
    (the dashed items immediately under it) with the new list. Lines that are
    not part of that list (other skills: sub-keys, or anything outside skills:)
    are left untouched.
    """
    cfg = os.path.join(home, "config.yaml")
    if not os.path.isfile(cfg):
        txt = "skills:\n  disabled: []\n"
    else:
        txt = open(cfg).read()

    lines = txt.splitlines(keepends=True)
    out = []
    i = 0
    n = len(lines)
    replaced = False
    while i < n:
        line = lines[i]
        # detect start of skills block (top-level 'skills:')
        if re.match(r"^skills:\s*$", line):
            out.append(line)
            i += 1
            # collect the indented block under skills:
            block = []
            while i < n and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                block.append(lines[i]); i += 1
            # within block, find 'disabled:' and its dashed list
            j = 0
            while j < len(block):
                bl = block[j]
                if re.match(r"\s*disabled:\s*$", bl):
                    out.append(bl)
                    j += 1
                    # skip existing dashed items
                    while j < len(block) and re.match(r"\s*-\s+", block[j]):
                        j += 1
                    # emit new list
                    for name in disabled:
                        out.append(f"    - {name}\n")
                    replaced = True
                else:
                    out.append(bl); j += 1
            continue
        out.append(line); i += 1

    if not replaced:
        # no skills block existed; append one
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append("skills:\n  disabled:\n")
        for name in disabled:
            out.append(f"    - {name}\n")

    open(cfg, "w").write("".join(out))

def rel_time(mtime: float) -> str:
    diff = datetime.now().timestamp() - mtime
    if diff < 0:
        diff = 0
    if diff < 60:
        return f"{int(diff)} detik lalu"
    if diff < 3600:
        return f"{int(diff//60)} menit lalu"
    if diff < 86400:
        return f"{int(diff//3600)} jam lalu"
    return f"{int(diff//86400)} hari lalu"

def skill_meta(skills_dir: str, relpath: str, manifest: dict, disabled: list) -> dict:
    """relpath = relative path from skills/ (e.g. 'software-development/plan')."""
    folder = os.path.join(skills_dir, relpath)
    skill_md = os.path.join(folder, "SKILL.md")
    mtime = os.path.getmtime(skill_md) if os.path.isfile(skill_md) else 0
    leaf = os.path.basename(relpath)
    category = os.path.dirname(relpath) or "(root)"
    builtin = leaf in manifest
    enabled = leaf not in disabled
    desc = ""
    if os.path.isfile(skill_md):
        txt = open(skill_md).read()
        dm = re.search(r"description:\s*(.+)", txt)
        if dm:
            desc = dm.group(1).strip().strip('"').strip("'")
    return {
        "name": relpath,
        "leaf": leaf,
        "category": category,
        "type": "builtin" if builtin else "custom",
        "enabled": enabled,
        "description": desc,
        "mtime": mtime,
        "rel": rel_time(mtime),
        "has_skill_md": os.path.isfile(skill_md),
    }

def walk_skills(skills_dir: str, manifest: dict, disabled: list) -> list:
    out = []
    if not os.path.isdir(skills_dir):
        return out
    for root, dirs, files in os.walk(skills_dir):
        if ".git" in root.split(os.sep):
            continue
        if "SKILL.md" in files:
            rel = os.path.relpath(root, skills_dir)
            out.append(skill_meta(skills_dir, rel, manifest, disabled))
    out.sort(key=lambda s: (s["category"], s["leaf"]))
    return out

def memory_meta(home: str, name: str) -> dict:
    # name is the file name (could be .md or .txt)
    fpath = os.path.join(home, "memories", name)
    mtime = os.path.getmtime(fpath) if os.path.isfile(fpath) else 0
    return {
        "name": name,
        "mtime": mtime,
        "rel": rel_time(mtime),
        "size": os.path.getsize(fpath) if os.path.isfile(fpath) else 0,
    }

def gather_data(profile: str) -> dict:
    home = profile_home(profile)
    manifest = read_manifest(home)
    disabled = read_disabled(home)
    skills_dir = os.path.join(home, "skills")
    memories_dir = os.path.join(home, "memories")
    skills = walk_skills(skills_dir, manifest, disabled)
    # Standard memory files Hermes uses everywhere (default profile has them).
    # Always show these fields even if the profile doesn't have the file yet,
    # so the user can create them by editing. Extra real files are appended too.
    STANDARD_MEM = ["MEMORY.md", "USER.md", "NOTES.md"]
    existing = {}
    if os.path.isdir(memories_dir):
        for f in sorted(os.listdir(memories_dir)):
            if f.startswith("."):
                continue
            if f.endswith(".lock") or ".deleted_" in f:
                continue
            fp = os.path.join(memories_dir, f)
            if os.path.isfile(fp) and f.lower().endswith((".md", ".txt", ".markdown")):
                existing[f] = memory_meta(home, f)
    memories = []
    for name in STANDARD_MEM:
        if name in existing:
            memories.append(existing[name])
        else:
            memories.append({
                "name": name,
                "mtime": 0,
                "rel": "belum ada",
                "size": 0,
                "empty": True,
            })
    for f, meta in existing.items():
        if f not in STANDARD_MEM:
            memories.append(meta)
    return {"profile": profile, "skills": skills, "memories": memories, "disabled": disabled}

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/" or u.path == "/index.html":
            try:
                html = open(os.path.join(os.path.dirname(__file__), "hermes-dashboard.html"), encoding="utf-8").read()
            except FileNotFoundError:
                html = "<h1>hermes-dashboard.html not found</h1>"
            return self._send(200, html, "text/html")
        qs = parse_qs(u.query)
        if u.path == "/api/profiles":
            return self._send(200, {"profiles": list_profiles()})
        if u.path == "/api/data":
            profile = qs.get("profile", ["default"])[0]
            return self._send(200, gather_data(profile))
        if u.path == "/api/raw":
            profile = qs.get("profile", ["default"])[0]
            kind = qs.get("kind", ["skill"])[0]
            name = qs.get("name", [""])[0]
            home = profile_home(profile)
            if kind == "skill":
                fp = os.path.join(home, "skills", name, "SKILL.md")
            else:
                fp = os.path.join(home, "memories", name)
            content = open(fp, encoding="utf-8").read() if os.path.isfile(fp) else ""
            return self._send(200, {"content": content})
        if u.path == "/api/sessions":
            import sqlite3 as _sq
            prof = qs.get("profile", ["default"])[0]
            db_path = os.path.join(HERMES_HOME, "state.db")
            out = []
            if os.path.isfile(db_path):
                try:
                    con = _sq.connect(db_path)
                    con.row_factory = _sq.Row
                    cur = con.cursor()
                    if prof == "default":
                        # Hermes stores default-profile sessions with profile_name NULL/'main'/''
                        cur.execute(
                            "SELECT id, session_key, title, profile_name, message_count, "
                            "last_activity_at, started_at, source FROM sessions "
                            "WHERE (archived IS NULL OR archived = 0) "
                            "AND (profile_name IS NULL OR profile_name = '' OR profile_name = 'main' OR profile_name = 'default') "
                            "ORDER BY last_activity_at DESC LIMIT 200"
                        )
                    else:
                        cur.execute(
                            "SELECT id, session_key, title, profile_name, message_count, "
                            "last_activity_at, started_at, source FROM sessions "
                            "WHERE (archived IS NULL OR archived = 0) AND profile_name = ? "
                            "ORDER BY last_activity_at DESC LIMIT 200",
                            (prof,)
                        )
                    for r in cur.fetchall():
                        out.append({
                            "id": r["id"],
                            "key": r["session_key"],
                            "title": r["title"] or "(tanpa judul)",
                            "profile": r["profile_name"] or "default",
                            "messages": r["message_count"] or 0,
                            "last": r["last_activity_at"] or 0,
                            "source": r["source"] or "",
                        })
                    con.close()
                except Exception as e:
                    out = [{"error": str(e)}]
            return self._send(200, {"sessions": out})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            data = self._json_body()
        except Exception:
            return self._send(400, {"error": "bad json"})
        if u.path == "/api/save":
            profile = data["profile"]; kind = data["kind"]; name = data.get("name", "").strip()
            content = data.get("content", "")
            new_name = data.get("new_name", "").strip()
            home = profile_home(profile)
            if not name and not new_name:
                return self._send(400, {"error": "nama tidak boleh kosong"})
            if kind == "skill":
                # name is relative path e.g. 'cat/leaf' (leaf required)
                target = new_name or name
                if "/" in target:
                    cat, leaf = target.rsplit("/", 1)
                else:
                    cat, leaf = "(custom)", target
                if not leaf:
                    return self._send(400, {"error": "nama skill (leaf) wajib"})
                # sanitize: no '..' segments
                if ".." in target.split("/"):
                    return self._send(400, {"error": "nama tidak valid"})
                fp = os.path.join(home, "skills", cat, leaf, "SKILL.md")
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                if not os.path.isfile(fp):
                    # create default frontmatter for a brand-new skill
                    content = f"---\nname: {leaf}\ndescription: {leaf} (skill buatan)\n---\n\n{content}".strip() + "\n"
                else:
                    shutil.copy(fp, fp + ".bak")
                open(fp, "w", encoding="utf-8").write(content)
                full = f"{cat}/{leaf}"
                return self._send(200, {"ok": True, "name": full})
            else:
                target = new_name or name
                if not target.lower().endswith((".md", ".txt", ".markdown")):
                    target += ".md"
                if "/" in target or ".." in target or target.startswith("."):
                    return self._send(400, {"error": "nama memory tidak valid"})
                fp = os.path.join(home, "memories", target)
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                if os.path.isfile(fp):
                    shutil.copy(fp, fp + ".bak")
                open(fp, "w", encoding="utf-8").write(content)
                return self._send(200, {"ok": True, "name": target})
        if u.path == "/api/delete":
            profile = data["profile"]; kind = data["kind"]; name = data["name"]
            home = profile_home(profile)
            if kind == "skill":
                fp = os.path.join(home, "skills", name)
                if os.path.isdir(fp):
                    shutil.rmtree(fp)
                    # remove now-empty parent category folder
                    parent = os.path.dirname(fp)
                    try:
                        if parent != os.path.join(home, "skills") and not os.listdir(parent):
                            os.rmdir(parent)
                    except OSError:
                        pass
            else:
                fp = os.path.join(home, "memories", name)
                if os.path.isfile(fp):
                    os.remove(fp)
            return self._send(200, {"ok": True})
        if u.path == "/api/toggle":
            profile = data["profile"]; skill = data["skill"]  # skill = relative path e.g. 'cat/leaf'
            home = profile_home(profile)
            leaf = os.path.basename(skill)
            disabled = read_disabled(home)
            if leaf in disabled:
                disabled.remove(leaf)
            else:
                disabled.append(leaf)
            write_disabled(home, disabled)
            return self._send(200, {"ok": True, "disabled": disabled})
        if u.path == "/api/session_delete":
            sid = data.get("id", "")
            if not sid:
                return self._send(400, {"error": "id kosong"})
            env = dict(os.environ); env["PATH"] = "/data/data/com.termux/files/usr/bin:" + env.get("PATH","")
            res = subprocess.run(
                ["/data/data/com.termux/files/usr/bin/hermes", "sessions", "delete", sid, "--yes"],
                capture_output=True, text=True, timeout=90,
                cwd=HERMES_HOME, env=env,
            )
            return self._send(200, {"ok": res.returncode == 0, "stderr": res.stderr[:300]})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass  # quiet

def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Hermes dashboard on http://127.0.0.1:{PORT}")
    server.serve_forever()

if __name__ == "__main__":
    main()
