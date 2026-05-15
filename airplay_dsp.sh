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

OUTPUT="${1:-hw:Headphones}"

exec ecasound -q --server -sr:44100 -b:2048 \
  -i:alsa,hw:Loopback,1 \
  -el:tap_dynamics_st,20,300,-10.5,3,0,9 \
  -o:alsa,"$OUTPUT" \
  -o:alsa,hw:2,0,1
