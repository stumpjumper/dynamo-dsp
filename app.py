#!/usr/bin/env python3
from flask import Flask, render_template, request, redirect, url_for, Response, stream_with_context
import subprocess
import re
import os
import signal
import threading
import struct
import math
import time
import json
import uuid
import alsaaudio

app = Flask(__name__)
_HERE         = os.path.dirname(os.path.abspath(__file__))
SCRIPT        = os.environ.get('AIRPLAY_DSP_SCRIPT', os.path.join(_HERE, 'airplay_dsp.sh'))
STATIONS_FILE = os.path.join(_HERE, 'stations.json')
STREAM_TIMEOUT = 3600   # seconds before auto-stop
PARAM_RE = re.compile(r'-el:tap_dynamics_st,([\d.-]+),([\d.-]+),([\d.-]+),([\d.-]+),([\d.-]+),([\d.-]+)')

DEFAULT_STATIONS = [
    ('kafa', 'KAFA 97.7',  'https://ice9.securenetsystems.net/KAFA'),
    ('kexp', 'KEXP 90.3',  'https://kexp-mp3-128.streamguys1.com/kexp128.mp3'),
    ('wfuv', 'WFUV 90.7',  'http://wfuv-onair.streamguys.org/onair-hi'),
    ('wrek', 'WREK 91.1',  'http://streaming.wrek.org:8000/main/320kb.mp3'),
]

# ── Station list (persistent JSON) ───────────────────────────────────────────

_stations = {}          # key -> (name, url)  — ordered by insertion
_stations_lock = threading.Lock()


def load_stations():
    global _stations
    try:
        with open(STATIONS_FILE) as f:
            data = json.load(f)
        s = {item['key']: (item['name'], item['url']) for item in data}
    except Exception:
        s = {k: (n, u) for k, n, u in DEFAULT_STATIONS}
    with _stations_lock:
        _stations = s
    if not os.path.exists(STATIONS_FILE):
        _save_stations_locked()


def _save_stations_locked():
    with _stations_lock:
        data = [{'key': k, 'name': v[0], 'url': v[1]} for k, v in _stations.items()]
    with open(STATIONS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def get_stations():
    with _stations_lock:
        return dict(_stations)


def add_station(name, url):
    key = uuid.uuid4().hex[:8]
    with _stations_lock:
        _stations[key] = (name, url)
    _save_stations_locked()
    return key


def delete_station(key):
    with _stations_lock:
        _stations.pop(key, None)
    _save_stations_locked()


load_stations()


# ── Audio level state ─────────────────────────────────────────────────────────

_level_db = -60.0
_level_l  = -60.0
_level_r  = -60.0
_level_lock = threading.Lock()


def audio_monitor():
    global _level_db, _level_l, _level_r
    inp = None
    while True:
        try:
            if inp is None:
                inp = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NONBLOCK,
                                    device='hw:2,1,1')
                inp.setchannels(2)
                inp.setrate(44100)
                inp.setformat(alsaaudio.PCM_FORMAT_S16_LE)
                inp.setperiodsize(1024)

            length, data = inp.read()
            if length > 0:
                samples = struct.unpack(str(len(data) // 2) + 'h', data)
                l_samps = samples[0::2]
                r_samps = samples[1::2]
                rms_l = (sum(s * s for s in l_samps) / len(l_samps)) ** 0.5
                rms_r = (sum(s * s for s in r_samps) / len(r_samps)) ** 0.5
                db_l = 20 * math.log10(max(rms_l / 32768.0, 1e-6))
                db_r = 20 * math.log10(max(rms_r / 32768.0, 1e-6))
                with _level_lock:
                    _level_l  = db_l
                    _level_r  = db_r
                    _level_db = (db_l + db_r) / 2
            else:
                with _level_lock:
                    _level_l  = max(_level_l  - 2.0, -60.0)
                    _level_r  = max(_level_r  - 2.0, -60.0)
                    _level_db = (_level_l + _level_r) / 2
                time.sleep(0.02)

        except Exception:
            inp = None
            with _level_lock:
                _level_db = -60.0
                _level_l  = -60.0
                _level_r  = -60.0
            time.sleep(1.0)


threading.Thread(target=audio_monitor, daemon=True).start()


# ── Stream timer ──────────────────────────────────────────────────────────────

_stream_end_time = None
_timer_lock = threading.Lock()


def timer_remaining():
    with _timer_lock:
        if _stream_end_time is None:
            return -1
        return max(0, int(_stream_end_time - time.time()))


def _set_timer():
    global _stream_end_time
    with _timer_lock:
        _stream_end_time = time.time() + STREAM_TIMEOUT


def _clear_timer():
    global _stream_end_time
    with _timer_lock:
        _stream_end_time = None


def timer_watchdog():
    while True:
        time.sleep(15)
        with _timer_lock:
            end = _stream_end_time
        if end is not None and time.time() >= end:
            stop_all_streams(stop_dsp=True)


threading.Thread(target=timer_watchdog, daemon=True).start()


# ── DSP / stream helpers ──────────────────────────────────────────────────────

def read_params():
    with open(SCRIPT) as f:
        content = f.read()
    m = PARAM_RE.search(content)
    if not m:
        return dict(attack=20, release=300, offset_gain=-6, makeup_gain=3, stereo_mode=0, function=9)
    return dict(
        attack=float(m.group(1)),
        release=float(m.group(2)),
        offset_gain=float(m.group(3)),
        makeup_gain=float(m.group(4)),
        stereo_mode=int(float(m.group(5))),
        function=int(float(m.group(6))),
    )


def write_params(attack, release, offset_gain, makeup_gain, stereo_mode, function):
    with open(SCRIPT) as f:
        content = f.read()
    replacement = f'-el:tap_dynamics_st,{attack},{release},{offset_gain},{makeup_gain},{stereo_mode},{function}'
    content = PARAM_RE.sub(replacement, content)
    with open(SCRIPT, 'w') as f:
        f.write(content)


def service_active():
    r = subprocess.run(['systemctl', 'is-active', 'airplay-dsp'], capture_output=True, text=True)
    return r.stdout.strip() == 'active'


def pid_file(key):
    return f'/tmp/stream-{key}.pid'


def stream_playing(key):
    try:
        pid = int(open(pid_file(key)).read().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def stream_start(key):
    stations = get_stations()
    if key not in stations:
        return
    _, url = stations[key]
    if not service_active():
        subprocess.run(['sudo', 'systemctl', 'start', 'airplay-dsp'])
    proc = subprocess.Popen(
        ['ffmpeg', '-reconnect', '1', '-reconnect_streamed', '1',
         '-reconnect_delay_max', '5', '-i', url,
         '-f', 'alsa', 'hw:Loopback,0'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with open(pid_file(key), 'w') as f:
        f.write(str(proc.pid))
    _set_timer()


def stream_stop(key):
    try:
        pid = int(open(pid_file(key)).read().strip())
        os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass
    try:
        os.remove(pid_file(key))
    except FileNotFoundError:
        pass
    _clear_timer()


def stop_all_streams(stop_dsp=False):
    for key in get_stations():
        try:
            pid = int(open(pid_file(key)).read().strip())
            os.kill(pid, signal.SIGTERM)
        except (FileNotFoundError, ValueError, ProcessLookupError):
            pass
        try:
            os.remove(pid_file(key))
        except FileNotFoundError:
            pass
    if stop_dsp:
        subprocess.run(['sudo', 'systemctl', 'stop', 'airplay-dsp'])
    _clear_timer()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    params = read_params()
    active = service_active()
    stations = get_stations()
    playing = {key: stream_playing(key) for key in stations}
    playing_key = next((k for k, v in playing.items() if v), None)
    return render_template('index.html', active=active, stations=stations,
                           playing=playing, playing_key=playing_key, **params)


@app.route('/levels')
def levels():
    def generate():
        try:
            while True:
                with _level_lock:
                    db   = _level_db
                    db_l = _level_l
                    db_r = _level_r
                params = read_params()
                offset = float(params['offset_gain'])
                center = -20.0 + offset
                delta  = db - center
                led = max(-5, min(5, int(delta / 3.0)))
                if db < -50:
                    led = 0
                remaining = timer_remaining()
                yield f'data: {led}:{db_l:.1f}:{db_r:.1f}:{remaining}\n\n'
                time.sleep(0.05)
        except GeneratorExit:
            pass
    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/apply', methods=['POST'])
def apply():
    write_params(
        attack=request.form['attack'],
        release=request.form['release'],
        offset_gain=request.form['offset_gain'],
        makeup_gain=request.form['makeup_gain'],
        stereo_mode=request.form['stereo_mode'],
        function=request.form['function'],
    )
    subprocess.run(['sudo', 'systemctl', 'restart', 'airplay-dsp'])
    return redirect(url_for('index'))


@app.route('/toggle', methods=['POST'])
def toggle():
    if service_active():
        subprocess.run(['sudo', 'systemctl', 'stop', 'airplay-dsp'])
    else:
        subprocess.run(['sudo', 'systemctl', 'start', 'airplay-dsp'])
    return redirect(url_for('index'))


@app.route('/stream/<key>', methods=['POST'])
def stream_toggle(key):
    stations = get_stations()
    if key not in stations:
        return redirect(url_for('index'))
    if stream_playing(key):
        stream_stop(key)
    else:
        stop_all_streams()
        stream_start(key)
    return redirect(url_for('index'))


@app.route('/stream_select', methods=['POST'])
def stream_select():
    key = request.form.get('station')
    stations = get_stations()
    if key not in stations:
        return redirect(url_for('index'))
    if stream_playing(key):
        stream_stop(key)
    else:
        stop_all_streams()
        stream_start(key)
    return redirect(url_for('index'))


@app.route('/timer/reset', methods=['POST'])
def timer_reset():
    if any(stream_playing(k) for k in get_stations()):
        _set_timer()
    return redirect(url_for('index'))


@app.route('/stations')
def stations_page():
    return render_template('stations.html', stations=get_stations())


@app.route('/stations/add', methods=['POST'])
def stations_add():
    name = request.form.get('name', '').strip()[:60]
    url  = request.form.get('url',  '').strip()[:500]
    if name and url.startswith(('http://', 'https://')):
        add_station(name, url)
    return redirect(url_for('stations_page'))


@app.route('/stations/delete/<key>', methods=['POST'])
def stations_delete(key):
    if stream_playing(key):
        stream_stop(key)
    delete_station(key)
    return redirect(url_for('stations_page'))


@app.route('/readme')
def readme():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'README.md')) as f:
            content = f.read()
    except FileNotFoundError:
        content = '_README not found._'
    return render_template('readme.html', content=content)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, threaded=True)
