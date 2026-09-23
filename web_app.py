from flask import Flask, render_template, request, jsonify, send_from_directory
from pathlib import Path
import subprocess, threading, json, os, time

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "output")))
OUT.mkdir(exist_ok=True)

app = Flask(__name__)
state = {"running": False, "message": "جاهز", "url": "", "started": None}

def worker(url):
    state["running"] = True
    state["message"] = "جاري تحميل وتحليل البث..."
    state["started"] = time.time()
    try:
        p = subprocess.Popen(
            ["python", "lavri_bot_v2.py", "--video", url],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        logs = []
        for line in p.stdout:
            logs.append(line.rstrip())
            if len(logs) > 200:
                logs = logs[-200:]
            state["message"] = line.strip() or state["message"]
            state["logs"] = logs
        p.wait()
        state["message"] = "اكتملت المعالجة" if p.returncode == 0 else "حدث خطأ أثناء المعالجة"
    except Exception as e:
        state["message"] = f"خطأ: {e}"
    finally:
        state["running"] = False

@app.route("/")
def index():
    return render_template("index.html")

@app.post("/api/process")
def process():
    if state["running"]:
        return jsonify(ok=False, error="يوجد فيديو قيد المعالجة"), 409
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(ok=False, error="ضع رابط YouTube"), 400
    state.update({"url": url, "logs": []})
    threading.Thread(target=worker, args=(url,), daemon=True).start()
    return jsonify(ok=True)

@app.get("/api/status")
def status():
    return jsonify(state)

@app.get("/api/files")
def files():
    items = []
    for p in OUT.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}:
            items.append({
                "name": p.name,
                "path": str(p.relative_to(OUT)).replace("\\","/")
            })
    return jsonify(items[-100:])

@app.get("/download/<path:filename>")
def download(filename):
    return send_from_directory(OUT, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=False)
