# rednote-bot

Rednote (小红书) video link ကို Telegram bot ဆီ ပို့လိုက်ရင် ဇာတ်လမ်းကို နားထောင်ပြီး မြန်မာ Recap video အဖြစ် ပြန်ပို့ပေးတဲ့ FastAPI bot ဖြစ်ပါတယ်။

## Bot UI / Commands

- `/start` — Welcome card နဲ့ feature menu ကို ပြပါတယ်။
- `/help` — Link ပို့ပုံ၊ commands နဲ့ အကြံပြုချက်တွေကို ပြပါတယ်။
- `/status` — Online/queue state၊ voice/provider နဲ့ output setting တွေကို ပြပါတယ်။
- `/clonevoice` — Voice message/audio sample ကို ElevenLabs Instant Voice Clone လုပ်ပြီး ဒီ chat အတွက် active လုပ်ပါတယ်။ Preview အသံလည်း ပြန်ပို့ပါတယ်။
- `/voicetest` — လက်ရှိရွေးထားတဲ့ narrator နဲ့ short Myanmar audio sample ထုတ်ပြီး Telegram မှာ ပြန်ပို့ပါတယ်။
- `/voice01` — Custom clone ကိုဖြုတ်ပြီး bundled Voice-01 default reference ကို ပြန်သုံးပါတယ်။
- `/usecharon` — ဒီ chat အတွက် voice clone ကို bypass လုပ်ပြီး Charon Deep voice ကို သုံးပါတယ်။
- Main menu မှာ **New Recap, Clone Voice, Voice-01, Voice Test, Voice Lab, Help, Status** inline buttons ပါပါတယ်။
- **Voice Lab** ထဲမှာ Clone Voice, Voice Test, Voice-01, Charon Deep, Main Menu, Status buttons ရှိပြီး active narrator ကိုပါ ပြပါတယ်။
- Job progress ကို `DOWNLOADING → SCRIPT WRITING → VOICEOVER → RENDERING → UPLOADING` structured card တစ်ခုထဲမှာ ပြပါတယ်။ Download/render မှာ **MB + progress bar**၊ voiceover မှာ segment count၊ upload မှာ part count နဲ့ delivered MB ပါပါတယ်။
- Progress silence **၆၀ စက္ကန့်** ရှိရင် watchdog က job task/file-size activity ကို စစ်ပြီး card မှာ အသိပေးပါတယ်။ Legitimate long-running job ကို silence တစ်ခုတည်းနဲ့ ချက်ချင်း fail မလုပ်ပါဘူး။
- Timeout/network/rate-limit အစရှိတဲ့ transient errors ကို configurable attempts နဲ့ automatic retry လုပ်ပါတယ်။ No audio/disk full/api key အစရှိတဲ့ permanent errors ကို automatic retry မလုပ်ပါဘူး။
- Job fail ဖြစ်ရင် **🔄 Retry Job** inline button ပါပါတယ်။ Button က မူရင်း link ကနေ job အသစ် restart လုပ်ပေးပြီး၊ retry data က memory-only ဖြစ်ပြီး TTL သတ်မှတ်ထားပါတယ်။ Server restart နောက်ပိုင်း expired button ဆိုရင် link ပြန်ပို့ရပါမယ်။
- Browser status page ကို responsive dark dashboard အဖြစ် `/` မှာ ပြထားပြီး၊ `/health` က Render health check အတွက်ပါ။

## Recap script နဲ့ အသံ

- ဇာတ်လမ်းထဲက စိတ်ဝင်စားစရာအချက်နဲ့ Hook စတင်ပြီး အကြောင်းအကျိုး၊ turning point နဲ့ အဆုံးကို ဆက်စပ်ရေးပါတယ်။
- Main character ကိုယ်တိုင်ပဲ ဇာတ်လမ်းတစ်ခုလုံးကို `ကျွန်တော်/ကျွန်မ/ငါ` **first-person** နဲ့ အစအဆုံး Recap ပြောပါတယ်။
- Narration၊ နောက်ကြောင်း၊ စိတ်ထဲအသံနဲ့ အဓိကစကားတွေအကုန် first-person voice အတည်းထားပြီး third-person narrator ကို မပြောင်းပါဘူး။
- Script narration window စုစုပေါင်းကို မူရင်း duration ရဲ့ **၈၀% ခန့်** ထိန်းပြီး၊ ပထမ ၈၀% ကိုပဲ ဖြတ်တာမဟုတ်ဘဲ ဇာတ်လမ်းအစ/အဆုံး အပြည့်အစုံကို အကျဉ်းချုပ်ပါတယ်။ ကျန် ၂၀% ကို pause/transition အဖြစ် ထားပါတယ်။
- စာပေသံစဉ်မဟုတ်ဘဲ သဘာဝကျတဲ့ မြန်မာအပြောစကား သုံးပါတယ်။ `DEEPSEEK_API_KEY` ရှိရင် DeepSeek က script ကို timing မပြောင်းဘဲ ထပ်ဆန်းပေးနိုင်ပါတယ်။
- Default narrator အဖြစ် ရွေးထားတဲ့ **Voice-01** ရဲ့ clean reference (`audio/voice01_clone_reference.wav`) ကို ElevenLabs Instant Voice Clone နဲ့ ပထမအသုံးပြုချိန်မှာ clone လုပ်ပြီး ရလာတဲ့ voice ID ကို cache ထားပါတယ်။ Arena preview ID ကို provider API မှာ တိုက်ရိုက်သုံးတာမဟုတ်ဘဲ၊ အဲဒီ 01 အသံရဲ့ reference audio ကို cloning အတွက် သုံးတာပါ။
- Cloned voice ရရင် အဲဒီအသံကို Recap အတွက် ဦးစားပေးသုံးပါတယ်။ Clone မရရင် **Gemini TTS `Charon`** → ElevenLabs deep male → Edge မြန်မာအသံ အစဉ်အတိုင်း fallback လုပ်ပါတယ်။
- Voice message/audio file ကို `/clonevoice` caption နဲ့ပို့တာ (သို့) audio ကို reply လုပ်ပြီး `/clonevoice` ပို့တာနဲ့ custom clone လုပ်နိုင်ပါတယ်။ အနည်းဆုံး ၁၀ စက္ကန့်၊ အများဆုံး 20MB လက်ခံပြီး ၃ မိနစ်ထက်ကြာရင် ပထမ ၃ မိနစ်ကို သုံးပါတယ်။ အကောင်းဆုံးအတွက် တစ်ယောက်တည်း၊ music မပါတဲ့ ၁–၂ မိနစ် sample သုံးပါ။ **ကိုယ့်အသံ သို့မဟုတ် ခွင့်ပြုချက်ရထားတဲ့ အသံကိုသာ clone လုပ်ပါ။**
- Custom clone က chat တစ်ခုချင်းစီအတွက် သုံးပြီး `/voice01` နဲ့ default သို့ ပြန်နိုင်ပါတယ်။ `data/voice_clones.json` မှာ voice ID/cache metadata ပဲ သိမ်းပြီး reference audio နဲ့ API key ကို မသိမ်းပါဘူး။
- **Voice Test** ခလုတ်/`/voicetest` က active narrator ကို resolve လုပ်ပြီး TTS sample တစ်ခုကို MP3 အဖြစ် ပြန်ပို့တဲ့အတွက် Recap မစခင် အသံကို အရင်စမ်းနိုင်ပါတယ်။ **Charon Deep** ခလုတ်/`/usecharon` နဲ့လည်း chat တစ်ခုချင်းစီမှာ voice clone မသုံးဘဲ Charon ကို ပြောင်းနိုင်ပါတယ်။

## GB ကြီးတဲ့ video နဲ့ output size

- မူလ ၁၀ မိနစ် limit ကို ဖြုတ်ထားပါတယ်။ `MAX_VIDEO_MINUTES` နဲ့ လိုအပ်ရင်သာ limit ပြန်ထားနိုင်ပါတယ်။
- Download/FFmpeg/Telegram upload timeout တွေကို GB ကြီးတဲ့ file တွေအတွက် configurable လုပ်ထားပါတယ်။
- ကြီးမားတဲ့ audio ကို RAM ထဲ အပြည့်မဆင့်ဘဲ disk-backed `memmap` နဲ့ bounded chunks တွေ သုံးပါတယ်။ Gemini inline limit ကျော်ရင် Files API ကို အသုံးပြုပါတယ်။
- Video duration ကို မလျှော့ဘဲ **final file size ကို မူရင်း၏ ၈၀% ခန့်** ဖြစ်အောင် bitrate တွက်ပြီး encode လုပ်ပါတယ်။ Size ကွာသွားရင် corrective pass တစ်ခု ထပ်လုပ်ပါတယ်။
- Final video က 50MB ကျော်ရင် **48,000,000 bytes အောက် playable MP4 အပိုင်းများ** ခွဲပြီး Telegram မှာ အစဉ်လိုက် အလိုအလျောက် ပို့ပါတယ်။
- ပို့ပြီးတဲ့ part နဲ့ intermediate file တွေကို ဖျက်ပြီး disk နေရာ ထပ်သုံးပါတယ်။

> Multi-GB job အတွက် Render instance ရဲ့ ephemeral disk၊ CPU/memory၊ processing time နဲ့ Gemini/ElevenLabs quota လုံလောက်ရပါမယ်။ Free instance တစ်ခုမှာ multi-GB video တွေ အမြဲလုပ်ဖို့မသင့်တော်တဲ့အပြင် TTS/API ကြေးလည်း အကြာချိန်နဲ့အတူ တိုးနိုင်ပါတယ်။

## Render deployment

Repository ထဲမှာ `Dockerfile` နဲ့ `render.yaml` ပါပြီးသားမို့ Render **Blueprint** နဲ့ deploy လုပ်နိုင်ပါတယ်။ အဲဒါမဟုတ်ရင် **New → Web Service → Docker** ကိုလည်း ရွေးနိုင်ပါတယ်။

### Environment variables

| Variable | လိုအပ်ချက် / အသုံးပြုချက် |
| --- | --- |
| `BOT_TOKEN` | **လိုအပ်တယ်** — Telegram BotFather token |
| `GEMINI_API_KEY` | **လိုအပ်တယ်** — Audio script generation နဲ့ Charon TTS |
| `DEEPSEEK_API_KEY` | Optional — Recap script polish |
| `ELEVENLABS_API_KEY` | Optional — Voice-01/custom cloning အတွက် လိုအပ်တယ်; Burmese TTS fallback အတွက်လည်း သုံးတယ် |
| `RENDER_EXTERNAL_URL` | Render က auto ထည့်ပေးတဲ့ public URL; Telegram webhook ချိတ်ရန် သုံးတယ် |
| `GEMINI_TTS_VOICE` | Default `Charon` |
| `GEMINI_TTS_MODELS` | Default `gemini-3.8-flash-tts,gemini-2.5-flash-tts` |
| `DEEPSEEK_MODEL` | Default `deepseek-flash` |
| `ELEVENLABS_MODEL` | Default `eleven_v4` |
| `ELEVENLABS_VOICE_ID` | Optional — သီးသန့် ElevenLabs custom/clone voice ID; cloning enabled ဆိုရင် auto Voice-01 ထက် ဦးစားပေးတယ် (`/voice01` က chat အလိုက် override လုပ်နိုင်တယ်) |
| `ELEVENLABS_VOICE_NAME` | Default `Adam`; voice ID မထည့်ရင် အဲဒီနာမည်နဲ့ ရှာတယ် |
| `TTS_PROVIDER` | Default `auto`; `gemini`, `elevenlabs`, `edge` ဖြင့် provider ကို explicit override လုပ်နိုင်ပြီး fallback ဆက်လုပ်တယ် |
| `VOICE_CLONE_ENABLED` | Default `true`; Voice-01/custom cloning ကို ဖွင့်/ပိတ်ရန် |
| `VOICE_CLONE_REFERENCE` | Default `audio/voice01_clone_reference.wav`; ကိုယ်ပိုင် default reference path ပြောင်းနိုင်တယ် |
| `VOICE_CLONE_CACHE_PATH` | Default `data/voice_clones.json`; cloned voice IDs နဲ့ per-chat selections သိမ်းရာနေရာ (persistent disk ရှိရင် path ပြောင်းနိုင်တယ်) |
| `VOICE_CLONE_MAX_BYTES` | Default `20000000`; Telegram reference အများဆုံး file size |
| `VOICE_CLONE_MAX_SECONDS` | Default `180`; reference ကို အဲဒီမိနစ်အထိ trim လုပ်တယ် |
| `FINAL_SIZE_RATIO` | Default `0.8` — မူရင်း file size ရဲ့ ၈၀% |
| `SCRIPT_DURATION_RATIO` | Default `0.8` — narration window budget; ဇာတ်လမ်းအဆုံးကိုတော့ မဖြတ်ဘူး |
| `MAX_VIDEO_MINUTES` | Default `0` — fixed duration limit မထားဘူး |
| `TELEGRAM_PART_LIMIT_BYTES` | Default `48000000` (48MB); safety အတွက် 48,000,000 bytes ထက် မကျော်စေဘူး |
| `DOWNLOAD_TIMEOUT` | Default `7200` seconds |
| `FFMPEG_TIMEOUT` | Default `7200` seconds |
| `TELEGRAM_UPLOAD_TIMEOUT` | Default `7200` seconds |
| `PROGRESS_EDIT_INTERVAL` | Default `3.0` seconds — Telegram progress edit throttle |
| `PROGRESS_WATCH_INTERVAL` | Default `5` seconds — watchdog polling interval |
| `PROGRESS_STALL_SECONDS` | Default `60` seconds — silence ဖြစ်ရင် watchdog check လုပ်ရန် |
| `JOB_MAX_ATTEMPTS` | Default `3` — transient stage failure အတွက် စုစုပေါင်း attempts |
| `JOB_RETRY_DELAY_SECONDS` | Default `5` — retry အကြား base delay; attempt အလိုက် တိုးတယ် |
| `RETRY_JOB_TTL_SECONDS` | Default `86400` — memory-only manual Retry button data သက်တမ်း |

> Render ရဲ့ default filesystem က ephemeral ဖြစ်လို့ redeploy/restart အလိုက် clone cache ပျောက်နိုင်ပါတယ်။ Persistent disk သုံးပြီး `VOICE_CLONE_CACHE_PATH` ကို ညှိတာ (သို့) ElevenLabs dashboard က ရထားတဲ့ cloned ID ကို `ELEVENLABS_VOICE_ID` မှာ ထည့်တာက clone အသစ်တွေ ထပ်မဆောက်မိအောင် ကူညီနိုင်ပါတယ်။

**API keys တွေကို `bot.py`၊ README သို့မဟုတ် Git repository ထဲ မထည့်ပါနဲ့။ Render Environment Variables / Secrets ထဲမှာသာ ထည့်ပါ။**

Health check path က `/health` ဖြစ်ပြီး service စတင်ပြီးသွားရင် Telegram webhook ကို `/webhook` ဆီ အလိုအလျောက် ချိတ်ပေးပါတယ်။

## Tests

API call မလုပ်ဘဲ script prompt၊ JSON parsing၊ PCM handling၊ voice-clone multipart/cache/provider priority၊ Telegram clone command၊ inline Voice Lab buttons၊ Voice Test audio upload၊ MB progress card၊ 60s watchdog၊ automatic/manual retry၊ target-size encoding correction နဲ့ Telegram splitting logic ကို စမ်းနိုင်ပါတယ်။

```bash
python -m unittest discover -s tests -v
```
