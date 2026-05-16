#!/bin/bash
# AirPlay DSP chain: loopback -> TAP Dynamics expander -> headphone jack
#
# tap_dynamics_st parameters:
#   1. Attack [ms]         20    (fast response to transients)
#   2. Release [ms]        300   (natural decay)
#   3. Offset Gain [dB]    -6    (lowers threshold — more signal gets expanded)
#   4. Makeup Gain [dB]    3     (restore average level after expansion)
#   5. Stereo Mode         0     (0=linked stereo, 1=L only, 2=R only)
#   6. Function            9     (8=expand 1:2 soft, 9=expand 1:3 soft, 10=expand 1:4 soft)
#
# Output device: hw:Headphones = 3.5mm jack
#                hw:vc4hdmi    = HDMI
#
# snd-aloop topology (card 0, pinned via /etc/modprobe.d/snd-aloop.conf):
#   Sources (shairport-sync, ffmpeg) write to hw:Loopback,1 (device 1 playback = pcm1p)
#   snd-aloop crosses:  pcm1p/subN  <->  pcm0c/subN
#   ecasound always opens pcm0c/sub0 (device 0 capture) regardless of device spec
#   ecasound monitor tap writes to pcm0p/sub0
#   snd-aloop crosses:  pcm0p/subN  <->  pcm1c/subN
#   Python VU meter reads pcm1c/sub0 via hw:0,1,0

OUTPUT="${1:-hw:Headphones}"

exec ecasound -q --server -sr:44100 -b:2048 \
  -i:alsa,hw:Loopback,0 \
  -el:tap_dynamics_st,20,300,-10.5,3,0,9 \
  -o:alsa,"$OUTPUT" \
  -o:alsa,hw:Loopback,0,1
