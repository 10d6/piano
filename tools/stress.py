#!/usr/bin/env python3
"""Random fast playing, to catch notes that don't stop.

Generates random passages (fast repeats, chords, overlapping repeats, pedal changes; every
note-on gets its note-off, keys A0-E6 so every key has a damper), renders each and reports any
that still sound 3 s after everything was released.

    python3 -I tools/stress.py build/piano_render 150
"""
import json, subprocess, numpy as np, soundfile as sf, sys, random
tool=sys.argv[1]; n=int(sys.argv[2])
# balanced: a key is released before it is struck again (a keyboard); overlap: a key can be struck
# again while still down, and each press gets its own note-off later (a sequencer)
mode=sys.argv[3] if len(sys.argv)>3 else "balanced"
fails=0
for seed in range(n):
    rnd=random.Random(seed); ev=[]; t=0.0; down={}
    pedal=False
    for step in range(rnd.randint(20,80)):
        t+=rnd.choice([0.0,0.0,0.005,0.01,0.03,0.06,0.12])
        r=rnd.random()
        if r<0.08:
            pedal=not pedal; ev.append({"t":round(t,4),"cc":64,"value":127 if pedal else 0})
        elif r<0.55:
            k=rnd.choice([rnd.randint(21,88), 60, 60, 36, 72])
            if mode=="balanced" and down.get(k,0)>0:
                ev.append({"t":round(t,4),"off":k}); down[k]-=1
                t+=rnd.choice([0.0,0.002,0.01])
            ev.append({"t":round(t,4),"on":k,"vel":rnd.randint(1,127)}); down[k]=down.get(k,0)+1
        else:
            held=[k for k,c in down.items() if c>0]
            if held:
                k=rnd.choice(held); ev.append({"t":round(t,4),"off":k}); down[k]-=1
    t+=0.05
    for k,c in down.items():
        for _ in range(c): ev.append({"t":round(t,4),"off":k})
    ev.append({"t":round(t,4),"cc":64,"value":0})
    end=t
    sc={"patch":"init","params":{"space.room":0},"length":round(end+4.0,3),"events":ev}
    open('/tmp/f.json','w').write(json.dumps(sc)); r=subprocess.run([tool,'/tmp/f.json','--out','/tmp/f.wav'],capture_output=True,text=True)
    y,sr=sf.read('/tmp/f.wav',always_2d=True); m=np.abs(y).max(1)
    tail=20*np.log10(m[int((end+3.0)*sr):].max()+1e-12)
    if tail>-80:
        fails+=1; json.dump(sc,open(f'/tmp/stuck_{seed}.json','w'))
        print(f"seed {seed}: still {tail:.1f} dBFS 3 s after everything was released ({len(ev)} events)")
print(f"{mode}: {fails} of {n} sequences left sound ringing 3 s after release")
