# CAD Quest

A Windows desktop game for practicing SolidWorks (or any 3D CAD program).
Gemini makes up a part, you model it on the clock, and then Gemini checks your model (free tier). English or Polish (EN / PL switch in the top-right corner).

## Run

```
pip install -r requirements.txt
python cad_quest.py
```

1. Paste your free Gemini API key (from aistudio.google.com → Get API key) and click **Save key**.
   The model list loads automatically; a "flash" model has the biggest free quota.
2. Pick a level and click **START**.
3. After the 3-2-1 countdown, the timer window stays on top of your CAD program.
4. Click **FINISH & upload** and choose screenshots (PNG/JPG, up to 6) and, if you want, an STL export.
   The app measures the STL (size, volume, area) and sends those numbers to Gemini together with the screenshots.

## Scoring

`points = (accuracy + time bonus) x level multiplier`. You only get the time bonus (up to +25) if you pass and finish within the time limit.
XP adds up into ranks: Sketch Rookie → Extrude Apprentice → Feature Engineer → Loft Specialist → CAD Legend.

Settings and history are saved in `%APPDATA%\CADQuest\`. The API key is stored there as plain text.

## Make an .exe (optional)

```
pip install pyinstaller
pyinstaller --onefile --windowed cad_quest.py
```
