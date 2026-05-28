#!/usr/bin/env python3
import os
import sys
import subprocess
import datetime
import threading
import wave
import sqlite3
import json
import time
import random
import re
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory


app = Flask(__name__)

# Constants
# Determine base directory dynamically based on app.py location (following symlinks)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
ACTIONS_LOG = os.path.join(BASE_DIR, "web", "actions.log")
WAV_PATH = os.path.join(TMP_DIR, "orison_radio.wav")
DB_PATH = os.path.join(BASE_DIR, "orison.db")

ORISON_BIN = "/usr/local/bin/orison"
BROADCAST_BIN = "/usr/local/bin/orison-broadcast"
broadcast_cancelled = False

# Ensure directories exist
os.makedirs(TMP_DIR, exist_ok=True)
if not os.path.exists(ACTIONS_LOG):
    with open(ACTIONS_LOG, "w") as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Log initialized.\n")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_time TEXT NOT NULL,
            action_type TEXT NOT NULL,
            params TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def is_broadcasting():
    """Checks if the pi_fm_rds process is active in the system."""
    try:
        output = subprocess.check_output(["pgrep", "-f", "pi_fm_rds"])
        return len(output) > 0
    except subprocess.CalledProcessError:
        return False

def get_last_command():
    """Reads the action log to find the last action taken."""
    if not os.path.exists(ACTIONS_LOG):
        return "None"
    try:
        with open(ACTIONS_LOG, "r") as f:
            lines = f.readlines()
        for line in reversed(lines):
            if "ACTION:" in line or "BACKGROUND ACTION:" in line or "BACKGROUND SEQUENCE BROADCAST:" in line:
                return line.strip()
    except Exception:
        pass
    return "None"

def trim_logs():
    """Keeps only the last 5 main log entries in the actions log file."""
    if not os.path.exists(ACTIONS_LOG):
        return
    try:
        with open(ACTIONS_LOG, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Matches main entry headers: [YYYY-MM-DD HH:MM:SS] followed by ACTION:, BACKGROUND ACTION:, etc.
        pattern = re.compile(
            r'^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] (?:ACTION:|BACKGROUND ACTION:|BACKGROUND SEQUENCE BROADCAST:|SCHEDULED BROADCAST TRIGGERED:|SCHEDULER ERROR:|Log initialized\.))',
            re.MULTILINE
        )
        matches = list(pattern.finditer(content))
        
        if len(matches) > 5:
            start_pos = matches[-5].start()
            trimmed_content = content[start_pos:]
            with open(ACTIONS_LOG, "w", encoding="utf-8") as f:
                f.write(trimmed_content)
    except Exception:
        pass

def get_recent_logs(num_lines=100):
    """Retrieves the last N lines of the action log for the dashboard."""
    if not os.path.exists(ACTIONS_LOG):
        return ""
    try:
        with open(ACTIONS_LOG, "r") as f:
            lines = f.readlines()
        return "".join(lines[-num_lines:])
    except Exception as e:
        return f"Error reading logs: {str(e)}"

def run_sync(cmd):
    """Runs a process synchronously, writing output to the action log."""
    trim_logs()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cmd_str = " ".join(cmd)
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
        if broadcast_cancelled and any(term in cmd_str for term in ["pkill", "orison-stop"]):
            status_str = "CANCELLED"
        else:
            status_str = "SUCCESS" if res.returncode == 0 else "FAILED"
        log_lines = [
            f"[{timestamp}] ACTION: {cmd_str}",
            f"  Status: {status_str} (exit code {res.returncode})"
        ]
        if res.stdout.strip():
            log_lines.append(f"  Stdout:\n{res.stdout.rstrip()}")
        if res.stderr.strip():
            log_lines.append(f"  Stderr:\n{res.stderr.rstrip()}")
        log_lines.append("--------------------------------------------------\n")
        
        log_entry = "\n".join(log_lines)
        with open(ACTIONS_LOG, "a") as f:
            f.write(log_entry)
        return res.returncode == 0, res.stdout + "\n" + res.stderr
    except Exception as e:
        log_entry = (
            f"[{timestamp}] ACTION: {cmd_str}\n"
            f"  Status: FAILED ({str(e)})\n"
            "--------------------------------------------------\n"
        )
        with open(ACTIONS_LOG, "a") as f:
            f.write(log_entry)
        return False, str(e)

def run_async(cmd):
    """Runs a process in the background, piping output straight to the action log."""
    trim_logs()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cmd_str = " ".join(cmd)
    try:
        log_file = open(ACTIONS_LOG, "a")
        log_file.write(f"[{timestamp}] BACKGROUND ACTION: {cmd_str}\n")
        log_file.flush()
        
        subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )
        return True, "Task launched in background."
    except Exception as e:
        log_entry = (
            f"[{timestamp}] BACKGROUND ACTION: {cmd_str}\n"
            f"  Status: LAUNCH FAILED ({str(e)})\n"
            "--------------------------------------------------\n"
        )
        with open(ACTIONS_LOG, "a") as f:
            f.write(log_entry)
        return False, str(e)


def compile_sequence(sequence, noise, filter_mode, morse_freq, morse_speed, log_file=None):
    """Shared helper function to compile a sequence of WAV parts and merge them with dynamic effects."""
    temp_files = []
    success = True
    cancelled = False
    
    # 1. Compile each part using `orison make`
    for idx, item in enumerate(sequence):
        if broadcast_cancelled:
            if log_file:
                log_file.write("  Aborting compilation: Cancelled by operator.\n")
            success = False
            cancelled = True
            break
            
        item_type = item.get("type")
        part_path = os.path.join(TMP_DIR, f"part_{idx}.wav")
        
        cmd = [
            ORISON_BIN, "make", item_type, 
            "--output", part_path, 
            "--filter", filter_mode, 
            "--morse-freq", morse_freq, 
            "--morse-speed", morse_speed, 
            "--no-silence"
        ]
        
        if item_type == "numbers":
            payload = item.get("groups") or str(item.get("count", "12"))
            cmd.append(payload)
        elif item_type == "say" or item_type == "morse":
            text = item.get("text", "").strip()[:500]
            cmd.append(text)
            
        if log_file:
            log_file.write(f"  Compiling segment {idx+1}/{len(sequence)}: {' '.join(cmd)}\n")
            log_file.flush()
            
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            if broadcast_cancelled:
                if log_file:
                    log_file.write(f"  Aborting compilation: Cancelled by operator during segment {idx+1}.\n")
                    log_file.flush()
                success = False
                cancelled = True
            else:
                if log_file:
                    log_file.write(f"  ERROR compiling segment {idx+1}: {res.stderr}\n")
                    log_file.flush()
                success = False
            break
        
        temp_files.append(part_path)

    if not success:
        # Cleanup
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except OSError: pass
        if cancelled:
            return False, "Segment compilation cancelled."
        return False, "Segment compilation failed."
        
    # 2. Append the 30-second silent tail to the end
    silence_part = os.path.join(TMP_DIR, "part_silence.wav")
    try:
        subprocess.run(["sox", "-n", "-r", "44100", "-c", "1", "-b", "16", silence_part, "trim", "0", "30.0"], check=True)
        temp_files.append(silence_part)
    except subprocess.CalledProcessError as e:
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except OSError: pass
        return False, f"Silence tail generation failed: {e}"

    # 3. Concatenate all segments into combined_clean.wav
    combined_clean = os.path.join(TMP_DIR, "combined_clean.wav")
    try:
        subprocess.run(["sox"] + temp_files + [combined_clean], check=True)
    except subprocess.CalledProcessError as e:
        for f in temp_files:
            if os.path.exists(f):
                try: os.remove(f)
                except OSError: pass
        return False, f"Concatenation failed: {e}"

    # 4. If noise is enabled, generate and mix noise
    if noise:
        try:
            # get duration of combined_clean.wav
            with wave.open(combined_clean, 'rb') as w:
                frames = w.getnframes()
                rate = w.getframerate()
                duration = frames / float(rate)
                
            noise_wav = os.path.join(TMP_DIR, "noise_seq.wav")
            noise_part1 = os.path.join(TMP_DIR, "noise_seq_p1.wav")
            noise_part2 = os.path.join(TMP_DIR, "noise_seq_p2.wav")
            subprocess.run([
                "sox", "-n", "-r", "44100", "-c", "1", "-b", "16", noise_part1,
                "synth", str(duration), "whitenoise", "vol", "0.03"
            ], check=True)
            subprocess.run([
                "sox", "-n", "-r", "44100", "-c", "1", "-b", "16", noise_part2,
                "synth", str(duration), "sine", "50", "vol", "0.01"
            ], check=True)
            subprocess.run(["sox", "-m", "-v", "1.0", noise_part1, "-v", "1.0", noise_part2, noise_wav], check=True)
            
            # mix
            subprocess.run(["sox", "-m", "-v", "1.0", combined_clean, "-v", "1.0", noise_wav, WAV_PATH], check=True)
            for f in [noise_wav, noise_part1, noise_part2]:
                if os.path.exists(f):
                    try: os.remove(f)
                    except OSError: pass
        except Exception as e:
            if os.path.exists(WAV_PATH): os.remove(WAV_PATH)
            os.rename(combined_clean, WAV_PATH)
    else:
        if os.path.exists(WAV_PATH): os.remove(WAV_PATH)
        os.rename(combined_clean, WAV_PATH)

    # 5. Cleanup part WAVs
    for f in temp_files:
        if os.path.exists(f):
            try: os.remove(f)
            except OSError: pass
    if os.path.exists(combined_clean):
        try: os.remove(combined_clean)
        except OSError: pass

    return True, "Sequence compilation successful."

def compile_and_broadcast_sequence_thread(sequence, ps, rt, noise, filter_mode, morse_freq, morse_speed, freq):
    """Wrapper function to compile sequence and then broadcast it asynchronously in a background thread."""
    global broadcast_cancelled
    trim_logs()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = open(ACTIONS_LOG, "a")
    log_file.write(f"[{timestamp}] BACKGROUND SEQUENCE BROADCAST: Commencing playlist compilation...\n")
    log_file.flush()
    
    if broadcast_cancelled:
        log_file.write("  Aborting: Cancelled by operator.\n")
        log_file.write("--------------------------------------------------\n")
        log_file.close()
        return
        
    success, msg = compile_sequence(sequence, noise, filter_mode, morse_freq, morse_speed, log_file)
    if not success:
        log_file.write(f"  Aborting broadcast: {msg}\n")
        log_file.write("--------------------------------------------------\n")
        log_file.close()
        return
        
    if broadcast_cancelled:
        log_file.write("  Aborting broadcast: Cancelled by operator during compilation.\n")
        log_file.write("--------------------------------------------------\n")
        log_file.close()
        return
        
    log_file.write("  Sequence compilation successful. Triggering FM broadcast...\n")
    log_file.flush()
    
    # Trigger broadcast
    broadcast_cmd = [BROADCAST_BIN, "--ps", ps, "--rt", rt, "--freq", freq]
    subprocess.Popen(
        broadcast_cmd,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True
    )
    log_file.close()

def execute_schedule_broadcast(action_type, params):
    """Executes a scheduled broadcast, first preempting any currently active broadcast."""
    global broadcast_cancelled
    broadcast_cancelled = False
    trim_logs()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = open(ACTIONS_LOG, "a")
    log_file.write(f"[{timestamp}] SCHEDULED BROADCAST TRIGGERED: Type={action_type}\n")
    log_file.flush()

    # Preemption: stop running broadcast processes
    subprocess.run(["sudo", "/usr/local/bin/orison-stop"])
    subprocess.run(["pkill", "-f", "/usr/local/bin/orison-broadcast"])
    subprocess.run(["pkill", "-f", "/usr/local/bin/orison"])

    # Extract parameters
    ps = params.get("ps", "ORISON").strip()
    rt = params.get("rt", "ATTENTION").strip()
    noise = params.get("noise") == True
    filter_mode = params.get("filter", "saturate").strip()
    morse_freq = str(params.get("morse_freq", "650")).strip()
    morse_speed = str(params.get("morse_speed", "0.09")).strip()
    freq = str(params.get("freq", "107.9")).strip()

    # Sanitize
    ps = "".join(c for c in ps if c.isalnum()).upper()[:8]
    if not ps: ps = "ORISON"
    rt = rt[:64]
    if not rt: rt = "ATTENTION"

    extra_args = [
        "--ps", ps,
        "--rt", rt,
        "--filter", filter_mode,
        "--morse-freq", morse_freq,
        "--morse-speed", morse_speed,
        "--freq", freq
    ]
    if noise:
        extra_args.append("--noise")

    if action_type == "sequence":
        sequence = params.get("sequence", [])
        if not sequence:
            log_file.write("  ERROR: Empty sequence in scheduled task.\n")
            log_file.write("--------------------------------------------------\n")
            log_file.close()
            return False
        
        threading.Thread(
            target=compile_and_broadcast_sequence_thread,
            args=(sequence, ps, rt, noise, filter_mode, morse_freq, morse_speed, freq),
            daemon=True
        ).start()
        log_file.close()
        return True

    elif action_type == "id":
        cmd = [ORISON_BIN, "id"] + extra_args
    elif action_type == "numbers":
        count = str(params.get("count", "12"))
        cmd = [ORISON_BIN, "numbers", count] + extra_args
    elif action_type == "say":
        text = params.get("text", "").strip()[:500]
        cmd = [ORISON_BIN, "say", text] + extra_args
    elif action_type == "morse":
        text = params.get("text", "ORISON ATTENTION").strip()[:500]
        cmd = [ORISON_BIN, "morse", text] + extra_args
    else:
        log_file.write(f"  ERROR: Unknown scheduled action type {action_type}\n")
        log_file.write("--------------------------------------------------\n")
        log_file.close()
        return False

    cmd_str = " ".join(cmd)
    try:
        log_file.write(f"  Executing scheduled background action: {cmd_str}\n")
        log_file.flush()
        subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )
        log_file.close()
        return True
    except Exception as e:
        log_file.write(f"  LAUNCH FAILED: {str(e)}\n")
        log_file.write("--------------------------------------------------\n")
        log_file.close()
        return False

def scheduler_loop():
    """Background polling loop querying the SQLite database for pending schedules."""
    while True:
        try:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM schedules WHERE status = 'pending' AND scheduled_time <= ?",
                (now_str,)
            )
            rows = cursor.fetchall()
            
            for row in rows:
                sched_id = row['id']
                action_type = row['action_type']
                try:
                    params = json.loads(row['params'])
                except Exception:
                    params = {}
                
                # Update status immediately to prevent duplicate triggers
                cursor.execute(
                    "UPDATE schedules SET status = 'completed' WHERE id = ?",
                    (sched_id,)
                )
                conn.commit()
                
                success = execute_schedule_broadcast(action_type, params)
                if not success:
                    cursor.execute(
                        "UPDATE schedules SET status = 'failed' WHERE id = ?",
                        (sched_id,)
                    )
                    conn.commit()
            conn.close()
        except Exception as e:
            try:
                trim_logs()
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(ACTIONS_LOG, "a") as f:
                    f.write(f"[{timestamp}] SCHEDULER ERROR: {str(e)}\n")
            except Exception:
                pass
        time.sleep(5)

@app.route("/")
def index():
    trim_logs()
    status = "Active" if is_broadcasting() else "Idle"
    last_cmd = get_last_command()
    recent_logs = get_recent_logs()
    wav_exists = os.path.exists(WAV_PATH)
    
    return render_template(
        "index.html", 
        status=status, 
        last_cmd=last_cmd, 
        recent_logs=recent_logs,
        wav_exists=wav_exists
    )

@app.route("/action", methods=["POST"])
def trigger_action():
    global broadcast_cancelled
    action = request.form.get("action")
    if not action:
        return jsonify({"success": False, "message": "No action provided."}), 400
        
    # Set/reset global broadcast_cancelled flag
    if action == "stop":
        broadcast_cancelled = True
    else:
        broadcast_cancelled = False
        
    success = False
    message = ""
    
    # Extract dynamic parameters
    ps = request.form.get("ps", "ORISON").strip()
    rt = request.form.get("rt", "ATTENTION").strip()
    noise = request.form.get("noise") == "true"
    filter_mode = request.form.get("filter", "saturate").strip()
    morse_freq = request.form.get("morse_freq", "650").strip()
    morse_speed = request.form.get("morse_speed", "0.09").strip()
    freq = request.form.get("freq", "107.9").strip()
    
    # Sanitize and validate options
    ps = "".join(c for c in ps if c.isalnum()).upper()[:8]
    if not ps: ps = "ORISON"
    rt = rt[:64]
    if not rt: rt = "ATTENTION"
    
    try:
        mf = int(morse_freq)
        if mf < 400 or mf > 1000: morse_freq = "650"
    except ValueError:
        morse_freq = "650"
        
    try:
        ms = float(morse_speed)
        if ms < 0.05 or ms > 0.20: morse_speed = "0.09"
    except ValueError:
        morse_speed = "0.09"

    try:
        freq_val = float(freq)
        if freq_val < 87.5 or freq_val > 108.0:
            freq = "107.9"
        else:
            freq = f"{freq_val:.1f}"
    except ValueError:
        freq = "107.9"
        
    extra_args = [
        "--ps", ps,
        "--rt", rt,
        "--filter", filter_mode,
        "--morse-freq", morse_freq,
        "--morse-speed", morse_speed,
        "--freq", freq
    ]
    if noise:
        extra_args.append("--noise")
        
    # 1. Broadcaster Stop Command
    if action == "stop":
        run_sync(["sudo", "/usr/local/bin/orison-stop"])
        run_sync(["pkill", "-f", "/usr/local/bin/orison-broadcast"])
        run_sync(["pkill", "-f", "/usr/local/bin/orison"])
        success = True
        message = "Broadcast stopped."
        
    # 2. Station ID and Broadcast
    elif action == "id":
        success, message = run_async([ORISON_BIN, "id"] + extra_args)
        
    elif action == "numbers":
        groups = request.form.get("groups", "").strip()
        if groups:
            success, message = run_async([ORISON_BIN, "numbers", groups] + extra_args)
        else:
            count = request.form.get("count", "12")
            try:
                val = int(count)
                if val < 1 or val > 100:
                    count = "12"
            except ValueError:
                count = "12"
            success, message = run_async([ORISON_BIN, "numbers", count] + extra_args)
        
    # 4. Say Arbitrary Text and Broadcast
    elif action == "say":
        text = request.form.get("text", "").strip()
        if not text:
            return jsonify({"success": False, "message": "Text content is required."}), 400
        text = text[:500]
        success, message = run_async([ORISON_BIN, "say", text] + extra_args)
        
    # 5. Morse Code and Broadcast
    elif action == "morse":
        text = request.form.get("text", "ORISON ATTENTION").strip()
        if not text:
            text = "ORISON ATTENTION"
        text = text[:500]
        success, message = run_async([ORISON_BIN, "morse", text] + extra_args)
        
    # 6. Make ID WAV Only (Synchronous)
    elif action == "make_id":
        success, msg_details = run_sync([ORISON_BIN, "make", "id"] + extra_args)
        message = "ID WAV generated successfully." if success else f"Generation failed: {msg_details}"
        
    # 7. Make Numbers WAV Only (Synchronous)
    elif action == "make_numbers":
        groups = request.form.get("groups", "").strip()
        if groups:
            success, msg_details = run_sync([ORISON_BIN, "make", "numbers", groups] + extra_args)
        else:
            count = request.form.get("count", "12")
            try:
                val = int(count)
                if val < 1 or val > 100:
                    count = "12"
            except ValueError:
                count = "12"
            success, msg_details = run_sync([ORISON_BIN, "make", "numbers", count] + extra_args)
        message = "Numbers WAV generated successfully." if success else f"Generation failed: {msg_details}"
        
    # 8. Make Say WAV Only (Synchronous)
    elif action == "make_say":
        text = request.form.get("text", "").strip()
        if not text:
            return jsonify({"success": False, "message": "Text content is required."}), 400
        text = text[:500]
        success, msg_details = run_sync([ORISON_BIN, "make", "say", text] + extra_args)
        message = "Voice WAV generated successfully." if success else f"Generation failed: {msg_details}"
        
    # 9. Make Morse WAV Only (Synchronous)
    elif action == "make_morse":
        text = request.form.get("text", "ORISON ATTENTION").strip()
        if not text:
            text = "ORISON ATTENTION"
        text = text[:500]
        success, msg_details = run_sync([ORISON_BIN, "make", "morse", text] + extra_args)
        message = "Morse WAV generated successfully." if success else f"Generation failed: {msg_details}"
        
    # 10. Broadcast Last Generated WAV
    elif action == "broadcast_last":
        if not os.path.exists(WAV_PATH):
            return jsonify({"success": False, "message": "No WAV file found to broadcast. Generate one first."}), 400
        success, message = run_async([BROADCAST_BIN, "--ps", ps, "--rt", rt, "--freq", freq])
        
    else:
        return jsonify({"success": False, "message": f"Unknown action: {action}"}), 400
        
    return jsonify({"success": success, "message": message})

@app.route("/action_sequence", methods=["POST"])
def trigger_sequence():
    """Endpoint to trigger a queued list of transmission blocks. Supports sync compile-only or async broadcast."""
    global broadcast_cancelled
    data = request.json
    if not data or "sequence" not in data:
        return jsonify({"success": False, "message": "No sequence payload provided."}), 400
        
    sequence = data["sequence"]
    if not isinstance(sequence, list) or not sequence:
        return jsonify({"success": False, "message": "Sequence must be a non-empty list."}), 400
        
    # Parse options
    ps = data.get("ps", "ORISON").strip()
    rt = data.get("rt", "ATTENTION").strip()
    noise = data.get("noise") == True or data.get("noise") == "true"
    filter_mode = data.get("filter", "saturate").strip()
    morse_freq = str(data.get("morse_freq", "650")).strip()
    morse_speed = str(data.get("morse_speed", "0.09")).strip()
    freq = str(data.get("freq", "107.9")).strip()
    broadcast = data.get("broadcast", True) # boolean flag: if False, compile but do not broadcast

    try:
        freq_val = float(freq)
        if freq_val < 87.5 or freq_val > 108.0:
            freq = "107.9"
        else:
            freq = f"{freq_val:.1f}"
    except ValueError:
        freq = "107.9"
    
    # Sanitize and validate options
    ps = "".join(c for c in ps if c.isalnum()).upper()[:8]
    if not ps: ps = "ORISON"
    rt = rt[:64]
    if not rt: rt = "ATTENTION"
    
    try:
        mf = int(morse_freq)
        if mf < 400 or mf > 1000: morse_freq = "650"
    except ValueError:
        morse_freq = "650"
        
    try:
        ms = float(morse_speed)
        if ms < 0.05 or ms > 0.20: morse_speed = "0.09"
    except ValueError:
        morse_speed = "0.09"

    lang = data.get("lang", "en").strip()

    if broadcast:
        broadcast_cancelled = False
        # Start dynamic compilation and broadcast thread (non-blocking)
        threading.Thread(
            target=compile_and_broadcast_sequence_thread,
            args=(sequence, ps, rt, noise, filter_mode, morse_freq, morse_speed, freq),
            daemon=True
        ).start()
        
        if lang == "tr":
            msg_out = f"{len(sequence)} aşamalı yayın sırası arka planda başlatıldı."
        else:
            msg_out = f"{len(sequence)}-stage broadcast sequence started in the background."
            
        return jsonify({"success": True, "message": msg_out})
    else:
        # Preview mode: compile synchronously in request thread so JavaScript receives response on completion
        success, msg = compile_sequence(sequence, noise, filter_mode, morse_freq, morse_speed)
        
        # Log the preview generation action
        trim_logs()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = (
            f"[{timestamp}] ACTION: Sequence Preview Generation (WAV only)\n"
            f"  Status: {'SUCCESS' if success else 'FAILED'}\n"
            f"  Message: {msg}\n"
            "--------------------------------------------------\n"
        )
        with open(ACTIONS_LOG, "a") as f:
            f.write(log_entry)
            
        if success:
            if lang == "tr":
                msg_out = "Yayın sırası önizleme sesi derlendi."
            else:
                msg_out = "Broadcast sequence preview audio compiled."
        else:
            msg_out = msg
            
        return jsonify({"success": success, "message": msg_out})

@app.route("/download")
def download_wav():
    if not os.path.exists(WAV_PATH):
        return "No broadcast WAV file exists yet. Generate one first.", 404
        
    as_attachment = request.args.get("download", "false").lower() == "true"
    return send_file(
        WAV_PATH,
        as_attachment=as_attachment,
        download_name="orison_radio.wav",
        mimetype="audio/wav"
    )

@app.route("/log")
def view_full_log():
    if not os.path.exists(ACTIONS_LOG):
        return "Log file not found."
    try:
        with open(ACTIONS_LOG, "r") as f:
            log_content = f.read()
        return log_content, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        return f"Error reading log: {str(e)}", 500

@app.route("/status")
def live_status():
    return jsonify({
        "status": "Active" if is_broadcasting() else "Idle",
        "last_cmd": get_last_command(),
        "recent_logs": get_recent_logs(),
        "wav_exists": os.path.exists(WAV_PATH)
    })

CHAR_TO_NUM = {
    ' ': '00', 'A': '01', 'B': '02', 'C': '03', 'D': '04', 'E': '05', 
    'F': '06', 'G': '07', 'H': '08', 'I': '09', 'J': '10', 'K': '11', 
    'L': '12', 'M': '13', 'N': '14', 'O': '15', 'P': '16', 'Q': '17', 
    'R': '18', 'S': '19', 'T': '20', 'U': '21', 'V': '22', 'W': '23', 
    'X': '24', 'Y': '25', 'Z': '26', '.': '27', '?': '28', '-': '29'
}

def otp_encrypt(text, key_digits=None):
    sanitized_text = ""
    for char in text.upper():
        if char in CHAR_TO_NUM:
            sanitized_text += char
            
    plain_digits = ""
    for char in sanitized_text:
        plain_digits += CHAR_TO_NUM[char]
        
    length = len(plain_digits)
    if length == 0:
        return "", ""
        
    if key_digits:
        key_digits = "".join(c for c in key_digits if c.isdigit())
        if len(key_digits) < length:
            key_digits += "".join(str(random.randint(0, 9)) for _ in range(length - len(key_digits)))
        else:
            key_digits = key_digits[:length]
    else:
        key_digits = "".join(str(random.randint(0, 9)) for _ in range(length))
        
    cipher_digits = ""
    for i in range(length):
        p = int(plain_digits[i])
        k = int(key_digits[i])
        c = (p + k) % 10
        cipher_digits += str(c)
        
    while len(cipher_digits) % 5 != 0:
        cipher_digits += "0"
        key_digits += "0"
        
    cipher_groups = [cipher_digits[i:i+5] for i in range(0, len(cipher_digits), 5)]
    key_groups = [key_digits[i:i+5] for i in range(0, len(key_digits), 5)]
    
    return " ".join(cipher_groups), " ".join(key_groups)

@app.route("/api/otp/encrypt", methods=["POST"])
def api_otp_encrypt():
    data = request.json
    lang = data.get("lang", "en").strip() if data else "en"
    if not data or "text" not in data:
        msg = "Mesaj metni gereklidir." if lang == "tr" else "Message text is required."
        return jsonify({"success": False, "message": msg}), 400
        
    text = data.get("text", "").strip()
    key = data.get("key", "").strip()
    
    if not text:
        msg = "Mesaj metni gereklidir." if lang == "tr" else "Message text is required."
        return jsonify({"success": False, "message": msg}), 400
        
    ciphertext, key_out = otp_encrypt(text, key)
    if not ciphertext:
        msg = "Desteklenmeyen karakterler içeren boş mesaj." if lang == "tr" else "Empty message with unsupported characters."
        return jsonify({"success": False, "message": msg}), 400
        
    return jsonify({"success": True, "ciphertext": ciphertext, "key": key_out})

@app.route("/api/otp/download_key", methods=["POST"])
def download_otp_key():
    import io
    ciphertext = request.form.get("ciphertext", "").strip()
    key = request.form.get("key", "").strip()
    lang = request.form.get("lang", "en").strip()
    
    if not ciphertext or not key:
        msg = "Eksik parametre: ciphertext veya key bulunamadı" if lang == "tr" else "Missing parameter: ciphertext or key not found"
        return msg, 400
        
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        "==================================================\n"
        "ORISON TACTICAL RADIO - DECRYPTION KEY SHEET (OTP)\n"
        "==================================================\n"
        f"Created: {timestamp}\n\n"
        "CIPHERTEXT (5-DIGIT GROUPS):\n"
        f"{ciphertext}\n\n"
        "ONE-TIME PAD KEY:\n"
        f"{key}\n\n"
        "DECRYPTION METHOD:\n"
        "1. Convert the ciphertext into a continuous digit stream.\n"
        "2. Convert the OTP key into a continuous digit stream.\n"
        "3. Subtract the key digit from the ciphertext digit, digit-by-digit.\n"
        "   If the result is negative, add 10 (Modulo 10 subtraction).\n"
        "   Formula: Plain = (Cipher - Key) % 10\n"
        "4. Read the resulting digit stream in pairs of 2:\n"
        "   00=Space, 01=A, 02=B, 03=C, 04=D, 05=E, 06=F, 07=G, 08=H,\n"
        "   09=I, 10=J, 11=K, 12=L, 13=M, 14=N, 15=O, 16=P, 17=Q, 18=R,\n"
        "   19=S, 20=T, 21=U, 22=V, 23=W, 24=X, 25=Y, 26=Z, 27=., 28=?, 29=-\n"
        "==================================================\n"
    )
    
    mem_file = io.BytesIO(content.encode("utf-8"))
    return send_file(
        mem_file,
        as_attachment=True,
        download_name="orison_otp_keysheet.txt",
        mimetype="text/plain"
    )

@app.route("/api/schedules")
def get_schedules():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM schedules WHERE status = 'pending' ORDER BY scheduled_time ASC LIMIT 50"
        )
        pending = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT * FROM schedules WHERE status != 'pending' ORDER BY scheduled_time DESC LIMIT 5"
        )
        history = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"success": True, "pending": pending, "history": history})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/schedules/add", methods=["POST"])
def add_schedule():
    data = request.json
    lang = data.get("lang", "en").strip() if data else "en"
    if not data:
        msg = "No payload provided."
        return jsonify({"success": False, "message": msg}), 400
        
    scheduled_time_str = data.get("scheduled_time")
    action_type = data.get("action_type")
    params = data.get("params")
    
    if not scheduled_time_str or not action_type or params is None:
        msg = "Missing required fields."
        return jsonify({"success": False, "message": msg}), 400
        
    try:
        if "T" in scheduled_time_str:
            dt = datetime.datetime.fromisoformat(scheduled_time_str)
        else:
            dt = datetime.datetime.strptime(scheduled_time_str, "%Y-%m-%d %H:%M:%S")
            
        now = datetime.datetime.now()
        if dt <= now:
            msg = "Zamanlanmış vakit geçmişte olamaz." if lang == "tr" else "Scheduled time cannot be in the past."
            return jsonify({"success": False, "message": msg}), 400
            
        db_time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        created_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedules (scheduled_time, action_type, params, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (db_time_str, action_type, json.dumps(params), created_at_str)
        )
        conn.commit()
        conn.close()
        msg = "Yayın başarıyla zamanlandı." if lang == "tr" else "Broadcast scheduled successfully."
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        prefix = "Hata" if lang == "tr" else "Error"
        return jsonify({"success": False, "message": f"{prefix}: {str(e)}"}), 500

@app.route("/api/schedules/delete/<int:sched_id>", methods=["POST"])
def delete_schedule(sched_id):
    lang = request.args.get("lang", "en").strip()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE id = ? AND status = 'pending'", (sched_id,))
        rows_deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if rows_deleted > 0:
            msg = "Zamanlanmış yayın iptal edildi." if lang == "tr" else "Scheduled broadcast cancelled."
            return jsonify({"success": True, "message": msg})
        else:
            msg = "Zamanlanmış yayın bulunamadı ya da zaten çalıştırıldı." if lang == "tr" else "Scheduled broadcast not found or already executed."
            return jsonify({"success": False, "message": msg}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/manifest.json")
def serve_manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def serve_sw():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

if __name__ == "__main__":
    # Start scheduler background thread
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8765, debug=False)
