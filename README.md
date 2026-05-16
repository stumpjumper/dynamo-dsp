# dynamo-dsp

A Raspberry Pi AirPlay 2 receiver with a software dynamic range expander — a
recreation of dbx 3BX-style "loudness restoration" for modern compressed audio.
Includes a mobile-friendly web UI for controlling radio streams, tuning the expander,
and monitoring levels in real time.

**Tested on:** Raspberry Pi 3, Raspbian 11 (Bullseye), Python 3.9.  
**Should work on:** Pi 3/4/5, any Raspbian/Raspberry Pi OS release from Bullseye onward.

---

## What it does

- Receives AirPlay 2 audio from any iPhone, iPad, or Mac on the same network
- Streams internet radio directly (no phone needed) — station list is editable via the UI
- Applies TAP Dynamics Stereo LADSPA expansion to every source — quiet passages get
  quieter, loud passages get louder, restoring dynamic range compressed out of modern
  recordings and streams
- Web UI at port 8080 (optional HTTPS via Tailscale) with:
  - Station dropdown — start/stop any stream in one tap
  - 60-minute sleep timer that stops radio and DSP automatically
  - Real-time L/R VU meters (works with both AirPlay and radio)
  - Gain-change display inspired by the dbx 3BX front panel
  - Live TAP Dynamics parameter tuning — changes apply instantly with no audio dropout
  - Editable station list — add/remove streams without touching config files

---

## Signal chain

```
iPhone / Mac (AirPlay 2)        Internet radio (ffmpeg)
        │                                │
  shairport-sync                      ffmpeg
        │                                │
        └──────────────┬─────────────────┘
                       ▼
               ALSA Loopback in  (hw:Loopback,0 — card 2, device 0)
                       │
               ALSA Loopback out (hw:Loopback,1 — card 2, device 1)
                       │
                   ecasound
                       │
          TAP Dynamics Stereo (LADSPA #2153)
                       │
              ┌─────────────────────────┐
              │                         │
       hw:Headphones              hw:2,0,1  (monitor tap)
      (3.5mm jack)                     │
              │                   hw:2,1,1  (Python RMS → SSE → browser meters)
      amplifier / speakers

         ecasound IAM (TCP 2868) ←── Flask /apply
         (live parameter updates, no dropout)
```

The ALSA loopback (snd-aloop) acts as a virtual patch cable. shairport-sync and ffmpeg
both write to it; ecasound reads from it, applies the expander, and writes to the
headphone output. A second loopback subdevice is used as a monitor tap so Python can
compute RMS levels for the browser meters without interfering with ecasound.

ecasound runs with `--server`, which opens an Interactive Audio Mode (IAM) control
socket on TCP port 2868 (localhost only). The Flask app connects to it when you click
"Apply" to update TAP Dynamics parameters live without restarting the audio chain.

---

## Hardware

| Item | Notes |
|------|-------|
| Raspberry Pi 3, 4, or 5 | Pi 3 works fine; Pi 4/5 give headroom |
| SD card (8 GB+) | Standard Raspberry Pi OS Lite (Bullseye or later) |
| 3.5mm audio output | Built-in jack on Pi 3/4; use a DAC HAT for better quality |
| Network connection | Wired recommended for AirPlay reliability |

Optional upgrade: HiFiBerry DAC2 Pro or similar I2S DAC HAT for cleaner analog output
via RCA to an amplifier's tape/aux input. Change the output device in `airplay_dsp.sh`.

---

## Prerequisites

### System packages

```bash
sudo apt update && sudo apt install -y \
    ecasound \
    ladspa-sdk \
    tap-plugins \
    ffmpeg \
    avahi-daemon \
    python3-pip \
    libportaudio2 \
    libasound2-dev
```

> **tap-plugins** provides the TAP Dynamics Stereo LADSPA plugin (ID 2153).
> Verify it's present: `analyseplugin /usr/lib/ladspa/tap_dynamics_st.so`

### shairport-sync (AirPlay 2)

AirPlay 2 requires a version of shairport-sync compiled with `--with-airplay-2` and
the companion `nqptp` daemon. The apt package in Bullseye is AirPlay 1 only — build
from source:

```bash
# nqptp (AirPlay 2 timing daemon)
sudo apt install -y libplist-dev
git clone https://github.com/mikebrady/nqptp.git
cd nqptp && autoreconf -fi && ./configure --with-systemd-startup
make && sudo make install
cd ..

# shairport-sync dependencies
sudo apt install -y \
    build-essential git autoconf automake libtool \
    libpopt-dev libconfig-dev libasound2-dev avahi-daemon libavahi-client-dev \
    libssl-dev libsoxr-dev libplist-dev libsodium-dev libavutil-dev \
    libavcodec-dev libavformat-dev uuid-dev libgcrypt-dev xxd

git clone https://github.com/mikebrady/shairport-sync.git
cd shairport-sync
autoreconf -fi
./configure --sysconfdir=/etc --with-alsa --with-soxr --with-avahi --with-ssl=openssl \
            --with-airplay-2 --with-systemd
make -j$(nproc) && sudo make install
cd ..
```

---

## Installation

### 1. ALSA loopback

Load the `snd-aloop` kernel module at boot:

```bash
echo 'snd-aloop' | sudo tee /etc/modules-load.d/snd-aloop.conf
sudo modprobe snd-aloop
```

Verify it appears as card 2 (or wherever — note the card number, you may need to
adjust `hw:2,0,1` / `hw:2,1,1` references in `app.py` and `airplay_dsp.sh` if
your system assigns a different number):

```bash
cat /proc/asound/cards
# Expected:  2 [Loopback]: Loopback - Loopback
```

### 2. Configure shairport-sync output

Edit `/etc/shairport-sync.conf` and set the ALSA output to the loopback:

```
alsa = {
  output_device = "hw:Loopback,0";
  mixer_control_name = "";
};
```

Then enable and start the services:

```bash
sudo systemctl enable nqptp shairport-sync
sudo systemctl start  nqptp shairport-sync
```

### 3. Clone the repo

```bash
cd ~
git clone https://github.com/stumpjumper/dynamo-dsp.git
```

### 4. Python dependencies

```bash
pip3 install -r ~/dynamo-dsp/requirements.txt
```

### 5. Install systemd services

Replace `USERNAME` with your Linux username throughout, then install:

```bash
USERNAME=$(whoami)
INSTALL_DIR="$HOME/dynamo-dsp"

for f in airplay-dsp dsp-ui; do
    sed "s|USERNAME|$USERNAME|g; s|/home/USERNAME/dynamo-dsp|$INSTALL_DIR|g" \
        ~/dynamo-dsp/systemd/${f}.service \
        | sudo tee /etc/systemd/system/${f}.service > /dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable airplay-dsp dsp-ui
sudo systemctl start  airplay-dsp dsp-ui
```

### 6. sudo for systemctl (required by the web UI)

The Flask app calls `sudo systemctl start/stop/restart airplay-dsp`. Allow this
without a password:

```bash
sudo cp ~/dynamo-dsp/systemd/sudoers-dynamo-dsp /etc/sudoers.d/dynamo-dsp
sudo sed -i "s/USERNAME/$(whoami)/g" /etc/sudoers.d/dynamo-dsp
sudo chmod 0440 /etc/sudoers.d/dynamo-dsp
```

Verify the syntax is valid before logging out:

```bash
sudo visudo -c
```

### 7. Initial station list

On first run, `app.py` creates `stations.json` from the built-in defaults (KAFA,
KEXP, WFUV, WREK). To start from the example file instead:

```bash
cp ~/dynamo-dsp/stations.json.example ~/dynamo-dsp/stations.json
```

Stations can also be added and removed at any time through the web UI (Edit link
next to the Stations header).

---

## Optional: HTTPS via Tailscale

Tailscale's `serve` command provides valid HTTPS certificates for your Tailscale
hostname, so the web UI works from anywhere without browser security warnings:

```bash
# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Expose the Flask app over HTTPS
sudo tailscale serve --bg 8080
```

After this the UI is reachable at both:
- `http://YOUR-PI-IP:8080` (LAN)
- `https://YOUR-HOSTNAME.taild6cb04.ts.net` (anywhere via Tailscale)

---

## Configuration

### Adjusting the DSP parameters

TAP Dynamics parameters live in `airplay_dsp.sh` and are read/written by the web UI.
Changes made in the UI are applied instantly via ecasound's IAM protocol when you
click "Apply" — no audio dropout. Parameters are also written to `airplay_dsp.sh`
so they persist across service restarts.

| Parameter | Default | Notes |
|-----------|---------|-------|
| Attack | 20 ms | How fast the expander reacts to a loud transient |
| Release | 300 ms | How long before the expander resets after a transient |
| Transition Level | −10.5 dB | Threshold around which expansion happens. Equivalent to the Transition Level knob on the dbx 3BX. Slider is reversed: drag **right** for a lower threshold (less aggressive, only the quietest passages affected); drag **left** for a higher threshold (more aggressive). Watch the Gain Change meter while adjusting. |
| Makeup Gain | 3 dB | Compensate average level after expansion |
| Stereo Mode | 0 (linked) | 0 = both channels track together, 1 = L only, 2 = R only |
| Function | 9 (1:3) | Expansion ratio: 8 = 1:2 gentle, 9 = 1:3 medium, 10 = 1:4 aggressive |

### ALSA card numbers

`airplay_dsp.sh` and `app.py` use named ALSA references (`hw:Loopback,x,x`) so they
are not sensitive to the loopback card's numeric index. If you ever see ecasound
crashing with `INVALIDARGUMENT`, check `aplay -l` to confirm the Loopback card is
present, and check `cat /proc/asound/cards` to verify `snd-aloop` loaded.

To pin the loopback to a fixed index (prevents card order from shifting on reboot):
```bash
echo "options snd-aloop index=0" | sudo tee /etc/modprobe.d/snd-aloop.conf
```

### Output device

`airplay_dsp.sh` defaults to `hw:Headphones` (the Pi's 3.5mm jack). To use HDMI:

```bash
sudo systemctl edit airplay-dsp
```

Add:
```ini
[Service]
ExecStart=
ExecStart=/home/USERNAME/dynamo-dsp/airplay_dsp.sh hw:vc4hdmi
```

For a HiFiBerry DAC:
```
ExecStart=/home/USERNAME/dynamo-dsp/airplay_dsp.sh hw:sndrpihifiberry
```

---

## Usage

Open `http://YOUR-PI-IP:8080` in a browser. The interface is mobile-optimized.

### Immediate controls (no restart)

| Control | Effect |
|---------|--------|
| Station dropdown + ▶ Play | Start selected radio stream (stops any current stream) |
| ⏹ Stop | Stop the current radio stream |
| Stop DSP / Start DSP | Stop or start the ecasound processing chain |

### Sleep timer

When a radio station starts, a 60-minute countdown appears. When it expires,
the stream **and** the DSP stop automatically. The ↺ Reset button restarts the
countdown. Manually stopping the stream clears the timer.

### TAP Dynamics (live, no dropout)

All TAP Dynamics sliders (Attack, Release, Transition Level, Makeup Gain) and the
Stereo Mode / Function dropdowns apply instantly via ecasound's IAM protocol. No
restart, no audio gap. Parameters are also saved to `airplay_dsp.sh` so they
survive a reboot.

### Level meters

**VU meters (L/R)** — Real-time signal level for both AirPlay and radio. Green =
quiet (below −9 dBFS), yellow = moderate (−9 to −4 dBFS), red = loud (above −4 dBFS).

**Gain Change** — Inspired by the dbx 3BX front panel. Amber LEDs (left) mean the
expander is attenuating quiet passages; red LEDs (right) mean it is boosting loud
ones. The center point tracks the Transition Level parameter.

---

## Services

| Service | Role | Restart command |
|---------|------|----------------|
| `airplay-dsp` | ecasound TAP Dynamics chain | `sudo systemctl restart airplay-dsp` |
| `dsp-ui` | Flask web interface (port 8080) | `sudo systemctl restart dsp-ui` |
| `shairport-sync` | AirPlay 2 receiver | `sudo systemctl restart shairport-sync` |
| `nqptp` | AirPlay 2 timing daemon | `sudo systemctl restart nqptp` |
| `tailscaled` | Tailscale VPN (if installed) | `sudo systemctl restart tailscaled` |

---

## Troubleshooting

**No audio from AirPlay**
- Check `sudo systemctl status shairport-sync nqptp`
- Verify shairport-sync output is `hw:Loopback,0` in `/etc/shairport-sync.conf`
- Verify `airplay-dsp` is running: `sudo systemctl status airplay-dsp`

**No audio from radio**
- Check `sudo systemctl status dsp-ui` — look for ffmpeg errors
- Test ffmpeg manually: `ffmpeg -i STREAM_URL -f alsa hw:Loopback,0`

**VU meters always dark**
- The loopback monitor tap (`hw:2,0,1`) is only written by ecasound when it's running
- Check `sudo systemctl status airplay-dsp`
- Verify the loopback card number matches in `app.py` and `airplay_dsp.sh`

**"Apply" has no effect**
- Check that `airplay-dsp` is running — IAM requires ecasound to be active: `sudo systemctl status airplay-dsp`
- If ecasound is running but Apply does nothing, verify it started with `--server` by checking `ps aux | grep ecasound`
- If the service is stopped, Apply falls back to `systemctl restart airplay-dsp` — check sudo access: `sudo visudo -c`

**Internal Server Error on the main page**
- Usually means `app.py` can't find `airplay_dsp.sh`. By default it looks in the same directory as `app.py`.
- If your install has the script in a different location, set the env var in the service:
  ```
  Environment=AIRPLAY_DSP_SCRIPT=/path/to/airplay_dsp.sh
  ```
  Add this line to `[Service]` in `/etc/systemd/system/dsp-ui.service`, then `sudo systemctl daemon-reload && sudo systemctl restart dsp-ui`.

**Web UI not reachable**
- Check `sudo systemctl status dsp-ui`
- Check port: `ss -tlnp | grep 8080`

---

## Project structure

```
dynamo-dsp/
├── app.py                    Flask web application
├── airplay_dsp.sh            ecasound DSP chain (also stores TAP Dynamics params)
├── requirements.txt          Python dependencies
├── stations.json             Persistent station list (created at first run)
├── stations.json.example     Example station list
├── templates/
│   ├── index.html            Main UI (VU meters, controls, DSP params)
│   ├── stations.html         Station editor
│   └── readme.html           In-app README viewer
└── systemd/
    ├── airplay-dsp.service   ecasound service unit
    ├── dsp-ui.service        Flask service unit
    └── sudoers-dynamo-dsp    sudoers rule for systemctl access
```

---

## Phase 2 (future ideas)

- Pi 4 + HiFiBerry DAC2 Pro HAT → RCA out to amplifier tape loop input
  (change output to `hw:sndrpihifiberry`, enable `dtoverlay=hifiberry-dacplus`)
- Multi-band expansion (3BX had three bands — needs LADSPA crossover + per-band processing)
- Alarm / scheduled start time
