RetroBard {version} - test build
================================

RetroBard reads the dialogue box of a retro game and speaks it with AI voices.
Thank you for trying it!


STARTING IT
-----------
1. Unzip the whole folder somewhere (for example Documents\RetroBard).
   Keep all the files together: RetroBard.exe needs the _internal folder next to it.
2. Double-click RetroBard.exe.
3. Windows may say "Windows protected your PC", because this test build is not
   signed. Click "More info", then "Run anyway".

A quick start guide opens the first time and walks you through the setup.
You can open it again any time with the "Quick start" button.


WHAT YOU NEED
-------------
- Windows 10 or 11.
- An emulator with the game. For Chrono Trigger the profile is made for snes9x.
  In the emulator, turn off smoothing filters and shaders.
- For voices, one of these:
  - your own ElevenLabs account and API key (a free account is enough to try it),
    or
  - a voice server that speaks the OpenAI speech API: OpenAI itself, or a free
    server running on your PC such as Kokoro-FastAPI, Speaches or LocalAI. On the
    Play tab choose "Custom server (OpenAI-compatible)", press "Server...", pick a
    preset, then "Test" and "Save".
  Without either you can still use "Silent test", which shows what is read
  without speaking it.
- RetroBard does not include any games or emulators. Use a game you own.


YOUR PRIVACY
------------
- RetroBard only captures the one game window you link, never the rest of your screen.
- The Setup tab lists your open windows' titles so you can pick the game. They are not saved.
- With ElevenLabs voices on, the dialogue text read from the game is sent to ElevenLabs
  using your API key. ElevenLabs may use it to improve its models unless you opt out in
  your ElevenLabs account (Terms and Privacy > Data use).
- With a custom voice server on, the dialogue text is sent to the server address
  you entered. A server on your own PC keeps it there; an online service follows
  its own terms.
- Your keys are kept in Windows Credential Manager. Spoken lines are cached in
  %APPDATA%\RetroBard\cache; you can clear them on the Options tab.
- "Check for updates" asks GitHub for the latest version, only when you press it.
- Apart from the voice service you choose and that button, nothing is sent
  anywhere, and nothing is ever sent to me.


UPDATING
--------
Press "About" -> "Check for updates" (also in the tray icon's menu). If a new
version is out, RetroBard can download it, close, replace its own folder and start
again. Your settings, letters and cache in %APPDATA%\RetroBard are kept.
If you installed RetroBard with the itch.io app, update it from the itch.io app.


SENDING BACK LETTERS YOU TAUGHT
-------------------------------
If RetroBard did not know some letters and you taught them on the Letters tab,
please send them to me: Letters tab -> "Export letters...". This saves a small
.json file with letter shapes only (no screenshots, no dialogue, no key).


WHERE YOUR FILES ARE
--------------------
Your settings, taught letters, cached voice lines and logs are in
    %APPDATA%\RetroBard
(paste that into the Explorer address bar). If something goes wrong, please send
me the file  logs\retrobard.log  from that folder. It contains no dialogue, and your
user folder is replaced with ~ in file paths.

To remove RetroBard: delete its folder and %APPDATA%\RetroBard, and remove
"RetroBard" from Windows Credential Manager if you saved a key for ElevenLabs or a voice server.
