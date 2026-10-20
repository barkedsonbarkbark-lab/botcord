# Botcord

A Discord-bot-token console app (Android, Kivy/KivyMD).

- Official Bot API (REST) + official Gateway (websocket) only - not a self-bot.
- Live chat: instant sending, replies, edit, delete, typing, unread markers, scroll-up history.
- Voice channels show live presence only (no audio; Discord needs E2EE/DAVE).
- Turn on "Message Content Intent" in the Developer Portal to read server messages.
- Saved logins are stored encrypted, locally on-device only.

## Getting the APK (works from Windows, no Linux needed)

1. Create an empty GitHub repository and push this folder to it
   (`git init`, `git add .`, `git commit -m init`, then push to `main`).
2. Open the repo's **Actions** tab -> "Build Android APK" (it also runs on every push;
   use "Run workflow" to start it by hand).
3. When it finishes (the first build takes a while), download **botcord-apk**
   from the run page, unzip it, and install the `.apk` on your phone.

On Linux/macOS you can also build locally: `buildozer -v android debug`.
