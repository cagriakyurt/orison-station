#!/usr/bin/env python3
import os
import sys
import subprocess
import datetime
import threading
import wave
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory

app = Flask(__name__)

# Constants
# Determine base directory dynamically based on app.py location (following symlinks)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
ACTIONS_LOG = os.path.join(BASE_DIR, "web", "actions.log")
WAV_PATH = os.path.join(TMP_DIR, "orison_radio.wav")

ORISON_BIN = "/usr/local/bin/orison"
BROADCAST_BIN = "/usr/local/bin/orison-broadcast"

# Ensure directories exist
os.makedirs(TMP_DIR, exist_ok=True)
if not os.path.exists(ACTIONS_LOG):
    with open(ACTIONS_LOG, "w") as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Log initialized.\n")

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

def get_recent_logs(num_lines=15):
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
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cmd_str = " ".join(cmd)
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
        log_entry = (
            f"[{timestamp}] ACTION: {cmd_str}\n"
            f"  Status: SUCCESS (exit code {res.returncode})\n"
            f"  Stdout:\n{res.stdout}\n"
            f"  Stderr:\n{res.stderr}\n"
            "--------------------------------------------------\n"
        )
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
    
    # 1. Compile each part using `orison make`
    for idx, item in enumerate(sequence):
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
            count = str(item.get("count", "12"))
            cmd.append(count)
        elif item_type == "say" or item_type == "morse":
            text = item.get("text", "").strip()[:500]
            cmd.append(text)
            
        if log_file:
            log_file.write(f"  Compiling segment {idx+1}/{len(sequence)}: {' '.join(cmd)}\n")
            log_file.flush()
            
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
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
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = open(ACTIONS_LOG, "a")
    log_file.write(f"[{timestamp}] BACKGROUND SEQUENCE BROADCAST: Commencing playlist compilation...\n")
    log_file.flush()
    
    success, msg = compile_sequence(sequence, noise, filter_mode, morse_freq, morse_speed, log_file)
    if not success:
        log_file.write(f"  Aborting broadcast: {msg}\n")
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

@app.route("/")
def index():
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
    action = request.form.get("action")
    if not action:
        return jsonify({"success": False, "message": "No action provided."}), 400
        
    success = False
    message = ""
    
    # Extract dynamic parameters
    ps = request.form.get("ps", "ORISON").strip()
    rt = request.form.get("rt", "ATTENTION").strip()
    noise = request.form.get("noise") == "true"
    filter_mode = request.form.get("filter", "am").strip()
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
        run_sync(["sudo", "/usr/bin/pkill", "-INT", "pi_fm_rds"])
        run_sync(["pkill", "-f", "orison-broadcast"])
        run_sync(["pkill", "-f", "orison"])
        success = True
        message = "Broadcast stopped."
        
    # 2. Station ID and Broadcast
    elif action == "id":
        success, message = run_async([ORISON_BIN, "id"] + extra_args)
        
    # 3. Numbers Sequence and Broadcast
    elif action == "numbers":
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
    filter_mode = data.get("filter", "am").strip()
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

    if broadcast:
        # Start dynamic compilation and broadcast thread (non-blocking)
        threading.Thread(
            target=compile_and_broadcast_sequence_thread,
            args=(sequence, ps, rt, noise, filter_mode, morse_freq, morse_speed, freq),
            daemon=True
        ).start()
        return jsonify({"success": True, "message": f"{len(sequence)} aşamalı yayın sırası arka planda başlatıldı."})
    else:
        # Preview mode: compile synchronously in request thread so JavaScript receives response on completion
        success, msg = compile_sequence(sequence, noise, filter_mode, morse_freq, morse_speed)
        
        # Log the preview generation action
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = (
            f"[{timestamp}] ACTION: Sequence Preview Generation (WAV only)\n"
            f"  Status: {'SUCCESS' if success else 'FAILED'}\n"
            f"  Message: {msg}\n"
            "--------------------------------------------------\n"
        )
        with open(ACTIONS_LOG, "a") as f:
            f.write(log_entry)
            
        return jsonify({"success": success, "message": "Yayın sırası önizleme sesi derlendi." if success else msg})

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

@app.route("/manifest.json")
def serve_manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def serve_sw():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False)
