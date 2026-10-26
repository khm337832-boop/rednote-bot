# rednote-bot

Rednote (小红书) video link ကို Telegram bot ဆီ ပို့လိုက်ရင် ဇာတ်လမ်းကို နားထောင်ပြီး မြန်မာ Recap video အဖြစ် ပြန်ပို့ပေးတဲ့ FastAPI bot ဖြစ်ပါတယ်။

## Bot UI / Commands

- `/start` — **DYY Agent** ကိုယ်တိုင် “ဆရာ” ဟုခေါ်ပြီး Rednote link ပို့ပုံကအစ အသုံးပြုပုံ၊ automatic workflow နဲ့ feature menu ကို ပြပါတယ်။ Activation command နောက်တစ်ခု ထပ်ရိုက်စရာမလိုပါဘူး။
- `/help` — Link ပို့ပုံ၊ commands နဲ့ အကြံပြုချက်တွေကို ပြပါတယ်။
- `/status` — Online/queue state၊ voice/provider နဲ့ output setting တွေကို ပြပါတယ်။
- `/clonevoice` — Voice message/audio sample ကို ElevenLabs Instant Voice Clone လုပ်ပြီး ဒီ chat အတွက် active လုပ်ပါတယ်။ Preview အသံလည်း ပြန်ပို့ပါတယ်။
- `/voicetest` — လက်ရှိရွေးထားတဲ့ narrator နဲ့ short Myanmar audio sample ထုတ်ပြီး Telegram မှာ ပြန်ပို့ပါတယ်။
- `/voice01` — Custom clone ကိုဖြုတ်ပြီး bundled Voice-01 default reference ကို ပြန်သုံးပါတယ်။
- `/usecharon` — ဒီ chat အတွက် voice clone ကို bypass လုပ်ပြီး Charon Deep voice ကို သုံးပါတယ်။
- Main menu မှာ **New Recap, Clone Voice, Voice-01, Voice Test, Voice Lab, Help, Status** inline buttons ပါပါတယ်။ DYY ON/OFF button မလိုအပ်တော့ပါဘူး။
- **Voice Lab** ထဲမှာ Clone Voice, Voice Test, Voice-01, Charon Deep, Main Menu, Status buttons ရှိပြီး active narrator ကိုပါ ပြပါတယ်။
- Job progress ကို `DOWNLOADING → SCRIPT WRITING → VOICEOVER → RENDERING → UPLOADING` structured card တစ်ခုထဲမှာ ပြပါတယ်။ Download/render မှာ **MB + progress bar**၊ voiceover မှာ segment count၊ upload မှာ part count နဲ့ delivered MB ပါပါတယ်။ Rendering မှာ FFmpeg ရဲ့ actual `out_time / total_size / speed` ကို ဖတ်ပြီး **timeline % + written MB + encoding speed** ကို live update လုပ်ပါတယ်။ Corrective pass ဆိုရင် pass 2/2 ကိုပါ ခွဲပြပါတယ်။
- Built-in **DYY Agent** က preference သို့မဟုတ် ON/OFF switch မပါဘဲ **အမြဲတမ်း active** ဖြစ်ပါတယ်။ `/start` ရဲ့ usage guide ကအစ၊ link လက်ခံ/queue၊ stage transitions၊ stage completion၊ retries၊ watchdog checks၊ errors နဲ့ final success အထိ user ကို **“ဆရာ”** ဟုခေါ်ကာ live report ပို့ပါတယ်။
- DYY က event context အလိုက် နောက်ဆက်အဆင့်ကို ရှင်းပြပါတယ်။ ဥပမာ—retry/watchdog အချိန်မှာ link ထပ်ပို့စရာမလိုကြောင်း၊ no-audio/private-link/config/storage error အမျိုးအစားအလိုက် သက်ဆိုင်ရာ next action ကို လမ်းညွှန်ပါတယ်။ ဒါက existing bot events ကိုသုံးတဲ့ built-in rule-based guidance ဖြစ်ပြီး added paid AI service/API key မလိုပါဘူး။
- မိနစ်ပိုင်းလိုက်/byte ပိုင်းလိုက်ပြောင်းတဲ့ frequent MB updates ကိုတော့ DYY message အများကြီး မပို့ဘဲ structured progress card ထဲမှာပဲ ထားပါတယ်။ Monitoring message ပို့တာ error တစ်ခု ဖြစ်ရင်လည်း Recap job ကို မရပ်စေပါဘူး။
- Progress silence **၆၀ စက္ကန့်** ရှိရင် watchdog က job task/file-size activity ကို စစ်ပြီး card မှာ အသိပေးပါတယ်။ Rendering process အတွက်ကိုက FFmpeg stats အသစ်မရတော့ဘဲ **CPU time လည်း မတက်တော့တဲ့ ၆၀ စက္ကန့်** ဖြစ်မှသာ stuck process ကို terminate/reap လုပ်ပြီး automatic retry လုပ်ပါတယ်။ CPU က ဆက်တက်နေတဲ့ legitimate long render ကို silence တစ်ခုတည်းနဲ့ ဖြတ်မထွင်းပါဘူး။
- Timeout/network/rate-limit အစရှိတဲ့ transient errors ကို configurable attempts နဲ့ automatic retry လုပ်ပါတယ်။ No audio/disk full/api key အစရှိတဲ့ permanent errors ကို automatic retry မလုပ်ပါဘူး။
- Job fail ဖြစ်ရင် **🔄 Retry Job** inline button ပါပါတယ်။ Button က မူရင်း link ကနေ job အသစ် restart လုပ်ပေးပြီး၊ retry data က memory-only ဖြစ်ပြီး TTL သတ်မှတ်ထားပါတယ်။ Server restart နောက်ပိုင်း expired button ဆိုရင် link ပြန်ပို့ရပါမယ်။
- Browser status page ကို responsive dark dashboard အဖြစ် `/` မှာ ပြထားပြီး၊ `/health` က Render health check အတွက်ပါ။

## Recap script နဲ့ အသံ

- ဇာတ်လမ်းထဲက စိတ်ဝင်စားစရာအချက်နဲ့ Hook စတင်ပြီး အကြောင်းအကျိုး၊ turning point နဲ့ အဆုံးကို ဆက်စပ်ရေးပါတယ်။
- Main character ကိုယ်တိုင်ပဲ ဇာတ်လမ်းတစ်ခုလုံးကို `ကျွန်တော်/ကျွန်မ/ငါ` **first-person** နဲ့ အစအဆုံး Recap ပြောပါတယ်။
- Narration၊ နောက်ကြောင်း၊ စိတ်ထဲအသံနဲ့ အဓိကစကားတွေအကုန် first-person voice အတည်းထားပြီး third-person narrator ကို မပြောင်းပါဘူး။
- Script narration window စုစုပေါင်းကို မူရင်း duration ရဲ့ **၈၀% ခန့်** ထိန်းပြီး၊ ပထမ ၈၀% ကိုပဲ ဖြတ်တာမဟုတ်ဘဲ ဇာတ်လမ်းအစ/အဆုံး အပြည့်အစုံကို အကျဉ်းချုပ်ပါတယ်။ ကျန် ၂၀% ကို pause/transition အဖြစ် ထားပါတယ်။
- Script generation မှာ Gemini primary model က စတင်ပြီး response က **20s ထက် ကြာ**သို့မဟုတ် fail ဖြစ်မှ backup helpers ကို တစ်ခုချင်း ထပ်စတင်ပါတယ်။ အများဆုံး **၃ model** က **မူရင်း prompt အတိအကျတစ်ခုထဲ** ကိုသုံးပြီး အရင်ရတဲ့ valid JSON draft ကို ယူ၊ ကျန် request တွေကို cancel လုပ်ပါတယ်။ Primary အမြန်ပြီးရင် helper လုံးဝမခေါ်ပါဘူး။
- ဒါက API key ၃ ခု အသစ်လိုတာမဟုတ်ဘဲ Gemini API key တစ်ခုထဲက model-level helpers ဖြစ်ပါတယ်။ Slow/failed job မှာ added requests ပါသွားနိုင်တဲ့အတွက် provider quota/cost သက်ရှိနိုင်ပြီး `GEMINI_SCRIPT_MAX_PARALLEL=1` နဲ့ တစ်ခုချင်း worker အတွင်း slow-response helpers ကို ပိတ်နိုင်ပါတယ်။ Pipeline တစ်ခုလုံးကို sequential လုပ်ချင်ရင်တော့ `PIPELINE_WORKERS=1` ကို သုံးပါ။
- စာပေသံစဉ်မဟုတ်ဘဲ သဘာဝကျတဲ့ မြန်မာအပြောစကား သုံးပါတယ်။ `DEEPSEEK_API_KEY` ရှိရင် DeepSeek က script ကို timing မပြောင်းဘဲ ထပ်ဆန်းပေးနိုင်ပြီး၊ polish က default **60s** ကျော်ရင် ရပြီးသား Gemini draft ကိုပဲ ဆက်သုံးပါတယ်။
- Default narrator အဖြစ် ရွေးထားတဲ့ **Voice-01** ရဲ့ clean reference (`audio/voice01_clone_reference.wav`) ကို ElevenLabs Instant Voice Clone နဲ့ ပထမအသုံးပြုချိန်မှာ clone လုပ်ပြီး ရလာတဲ့ voice ID ကို cache ထားပါတယ်။ Arena preview ID ကို provider API မှာ တိုက်ရိုက်သုံးတာမဟုတ်ဘဲ၊ အဲဒီ 01 အသံရဲ့ reference audio ကို cloning အတွက် သုံးတာပါ။
- Cloned voice ရရင် အဲဒီအသံကို Recap အတွက် ဦးစားပေးသုံးပါတယ်။ Clone မရရင် **Gemini TTS `Charon`** → ElevenLabs deep male → Edge မြန်မာအသံ အစဉ်အတိုင်း fallback လုပ်ပါတယ်။
- Voice message/audio file ကို `/clonevoice` caption နဲ့ပို့တာ (သို့) audio ကို reply လုပ်ပြီး `/clonevoice` ပို့တာနဲ့ custom clone လုပ်နိုင်ပါတယ်။ အနည်းဆုံး ၁၀ စက္ကန့်၊ အများဆုံး 20MB လက်ခံပြီး ၃ မိနစ်ထက်ကြာရင် ပထမ ၃ မိနစ်ကို သုံးပါတယ်။ အကောင်းဆုံးအတွက် တစ်ယောက်တည်း၊ music မပါတဲ့ ၁–၂ မိနစ် sample သုံးပါ။ **ကိုယ့်အသံ သို့မဟုတ် ခွင့်ပြုချက်ရထားတဲ့ အသံကိုသာ clone လုပ်ပါ။**
- Custom clone က chat တစ်ခုချင်းစီအတွက် သုံးပြီး `/voice01` နဲ့ default သို့ ပြန်နိုင်ပါတယ်။ `data/voice_clones.json` မှာ voice ID/cache metadata ပဲ သိမ်းပြီး reference audio နဲ့ API key ကို မသိမ်းပါဘူး။
- **Voice Test** ခလုတ်/`/voicetest` က active narrator ကို resolve လုပ်ပြီး TTS sample တစ်ခုကို MP3 အဖြစ် ပြန်ပို့တဲ့အတွက် Recap မစခင် အသံကို အရင်စမ်းနိုင်ပါတယ်။ **Charon Deep** ခလုတ်/`/usecharon` နဲ့လည်း chat တစ်ခုချင်းစီမှာ voice clone မသုံးဘဲ Charon ကို ပြောင်းနိုင်ပါတယ်။

## Four-worker quality pipeline

- Default `PIPELINE_WORKERS=4` ဖြစ်ပြီး အများဆုံး ၄ ခုအထိသာ ခွင့်ပြုပါတယ်။ Speed တစ်ခုထဲအစား အပိုင်းတစ်ခုချင်း စិစစ်အောင် လုပ်ဖို့ ရည်ရွယ်ထားတဲ့အတွက် processing time၊ provider request အရေအတွက်၊ CPU နဲ့ disk usage တိုးနိုင်ပါတယ်။ `PIPELINE_WORKERS=1` ဆိုရင် Script → Voiceover → Rendering အားလုံးမှာ ယခင် sequential path ကို သုံးပါတယ်။
- **Script:** Lead editor တစ်ခုက audio အပြည့်ကို နားထောင်ပြီး shared story map အရင်လုပ်ပါတယ်။ Gemini audio reference ကို တစ်ခါပဲ prepare လုပ်ပြီး worker ၄ ခုက မတူညီတဲ့ timeline ranges ကို shared map နဲ့ original first-person prompt အတိုင်း ရေးပါတယ်။ Range အပြည့်မရသေးတာ/invalid blocks ဆိုရင် corrective retry တစ်ခါလုပ်ပြီး၊ မအောင်မြင်ရင် incomplete script ကို မလက်ခံဘဲ one-pass full-story fallback ကို သုံးပါတယ်။ နောက်ဆုံးမှာ worker order အတိုင်း merge လုပ်ပြီး timestamps နဲ့ ၈၀% narration window ကို ထပ်စစ်ပါတယ်။
- **Voiceover:** Locked script ပြီးမှသာ စတင်ပါတယ်။ ပထမ segment က provider/resolved Voice-01 ID ကို lock လုပ်ပြီး နောက် segment တွေက အဲဒီ setting ကို share ပါတယ်။ TTS synthesis နဲ့ silence trimming ကို တစ်ခါအများဆုံး ၄ segment လုပ်ပြီး၊ ordered mixer တစ်ခုကပဲ timing/atempo/fade/PCM placement ကို script order အတိုင်း ဆက်လုပ်ပါတယ်။ Temporary prepared files ကို batch အလိုက် ရှင်းတဲ့အတွက် video ရှည်လေလေ RAM ထဲမှာ segment အကုန်စုပ်တာမဟုတ်ပါဘူး။ Gemini/ElevenLabs/Edge fallback တွေကို ဆက်ထားပါတယ်။
- **Rendering:** Timeline ကို ordered parts ၄ ခုခွဲပြီး bitrate/resolution/codec/audio settings ကို global တစ်ခုအတိုင်း သုံးပါတယ်။ Video နဲ့ dubbed audio နှစ်ခုလုံးကို တူညီတဲ့ range မှာ seek လုပ်ပြီး part တစ်ခုချင်းရဲ့ duration/audio/video ကို စစ်ပါတယ်။ ပြီးမှ concat list အတိုင်း stream-copy join လုပ်ကာ joined duration နဲ့ streams ကို ထပ်စစ်ပါတယ်။ Target size ကွာရင် corrective pass 2/2 လုပ်ပြီး parallel path မအောင်မြင်ရင် original sequential encoder ကို fallback လုပ်ပါတယ်။ Telegram-safe ordered splitting က မူလအတိုင်းပါပါတယ်။
- Script/Voiceover/Render worker counts၊ completed parts၊ aggregate timeline %/speed/MB၊ ordered join နဲ့ validation ကို structured card တစ်ခုထဲမှာပဲ ပြပါတယ်။ DYY ကလည်း ထို့ card/events ကိုပဲ ဆက်စစ်ပြီး message အသစ်အများကြီး မပို့ပါဘူး။
- Render worker တစ်ခုချင်းရဲ့ x264 threads ကို default `1` ထားပြီး shared/container CPU မှာ host CPU count ကိုကြည့်ပြီး oversubscribe မဖြစ်စေပါဘူး။ Guaranteed CPU ပိုရှိတဲ့ deployment ဆိုရင် `RENDER_THREADS_PER_WORKER` ကို တိုးနိုင်ပါတယ်။ Corrective parallel attempt တစ်ခုမှာ previous best + current parts + current joined file တို့ တစ်ပြိုင်နက်ရှိနိုင်တဲ့အတွက် handler က target size ရဲ့ ၃ ဆခန့် + audio scratch ကို ကြိုတင် reserve စစ်ပါတယ်။

> Worker ၄ ခုက API request ၄ ခုကို တစ်ပြိုင်နက် ထုတ်နိုင်ပါတယ်။ Script story map၊ corrective retries၊ optional DeepSeek polish နဲ့ slow-model helpers ဟာ quota/cost ကို ထပ်တိုးစေနိုင်ပါတယ်။ Provider rate limit ကြည့်ပြီး `PIPELINE_WORKERS`၊ `GEMINI_SCRIPT_MAX_PARALLEL` နဲ့ `RENDER_THREADS_PER_WORKER` ကို instance နဲ့ ကိုက်ညီအောင် ညှိပါ။

## Source credit နဲ့ copyright context

- Delivered video ရဲ့ **ပထမ part caption** မှာ မူရင်း source link နဲ့ first-person recap/commentary ဖြစ်ကြောင်း၊ မူရင်းပိုင်ဆိုင်မှုများသည် မူရင်းပိုင်ရှင်များဆီသာဖြစ်ကြောင်း rights notice ထည့်ပါတယ်။ Part အများကြီးဖြစ်ရင် နောက်ပိုင်း captions ကို short ထားပြီး ordered delivery ကို ဆက်လုပ်ပါတယ်။
- ဒီ attribution/notice က **100% copyright shield မဟုတ်ပါဘူး**။ Content ID/copyright claim၊ takedown သို့မဟုတ် တရားဝင်တာဝန်ကို အလိုအလျောက် ကာကွယ်ပေးတာလည်း မဟုတ်ပါဘူး။ ခွင့်ပြုချက်ရထားတဲ့၊ ကိုယ်ပိုင်၊ public-domain သို့မဟုတ် applicable law အရ အသုံးပြုခွင့်ရှိတဲ့ source ကိုသာ သုံးပါ။
- `SOURCE_CREDIT_ENABLED=false` နဲ့ source link ကို၊ `COPYRIGHT_NOTICE_ENABLED=false` နဲ့ rights notice ကို သီးသန့်ပိတ်နိုင်ပါတယ်။ ပိတ်ထားတာကလည်း copyright status ကို မပြောင်းပါဘူး။

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
| `GEMINI_SCRIPT_MAX_PARALLEL` | Default `3` (cap 3) — တစ်ခုချင်း generation ရဲ့ primary + slow-response helpers; `1` ဖြင့် အဲဒီ helpers ကို ပိတ်နိုင်တယ် (pipeline အပြည့် sequential အတွက် `PIPELINE_WORKERS=1`) |
| `GEMINI_SCRIPT_HELPER_DELAY_SECONDS` | Default `20` — primary/helper response ကို စောင့်ပြီး နောက် helper တစ်ခု ထပ်စတင်ရန် |
| `GEMINI_SCRIPT_MODEL_TIMEOUT` | Default `180` seconds — model request တစ်ခုချင်းရဲ့ timeout |
| `DEEPSEEK_POLISH_TIMEOUT` | Default `60` seconds — optional polish ကြာရင် Gemini draft ကို ဆက်သုံးရန် |
| `PIPELINE_WORKERS` | Default `4`, cap `4` — Script/Voiceover/Rendering အတွက် shared worker limit; `1` ဖြင့် pipeline အပြည့် sequential fallback |
| `RENDER_THREADS_PER_WORKER` | Default `1` — render worker တစ်ခုချင်းရဲ့ x264 threads; guaranteed CPU ပိုရှိရင် positive integer အဖြစ် တိုးနိုင်တယ် |
| `ELEVENLABS_MODEL` | Default `eleven_v4` |
| `ELEVENLABS_VOICE_ID` | Optional — သီးသန့် ElevenLabs custom/clone voice ID; cloning enabled ဆိုရင် auto Voice-01 ထက် ဦးစားပေးတယ် (`/voice01` က chat အလိုက် override လုပ်နိုင်တယ်) |
| `ELEVENLABS_VOICE_NAME` | Default `Adam`; voice ID မထည့်ရင် အဲဒီနာမည်နဲ့ ရှာတယ် |
| `TTS_PROVIDER` | Default `auto`; `gemini`, `elevenlabs`, `edge` ဖြင့် provider ကို explicit override လုပ်နိုင်ပြီး fallback ဆက်လုပ်တယ် |
| `VOICE_CLONE_ENABLED` | Default `true`; Voice-01/custom cloning ကို ဖွင့်/ပိတ်ရန် |
| `VOICE_CLONE_REFERENCE` | Default `audio/voice01_clone_reference.wav`; ကိုယ်ပိုင် default reference path ပြောင်းနိုင်တယ် |
| `VOICE_CLONE_CACHE_PATH` | Default `data/voice_clones.json`; cloned voice IDs နဲ့ per-chat selections သိမ်းရာနေရာ (persistent disk ရှိရင် path ပြောင်းနိုင်တယ်) |
| `VOICE_CLONE_MAX_BYTES` | Default `20000000`; Telegram reference အများဆုံး file size |
| `VOICE_CLONE_MAX_SECONDS` | Default `180`; reference ကို အဲဒီမိနစ်အထိ trim လုပ်တယ် |
| `SOURCE_CREDIT_ENABLED` | Default `true`; delivered ပထမ part caption မှာ မူရင်း source link ထည့်ရန် |
| `COPYRIGHT_NOTICE_ENABLED` | Default `true`; delivered ပထမ part caption မှာ recap/rights context ထည့်ရန်; copyright immunity ကို မပေးပါဘူး |
| `FINAL_SIZE_RATIO` | Default `0.8` — မူရင်း file size ရဲ့ ၈၀% |
| `SCRIPT_DURATION_RATIO` | Default `0.8` — narration window budget; ဇာတ်လမ်းအဆုံးကိုတော့ မဖြတ်ဘူး |
| `MAX_VIDEO_MINUTES` | Default `0` — fixed duration limit မထားဘူး |
| `TELEGRAM_PART_LIMIT_BYTES` | Default `48000000` (48MB); safety အတွက် 48,000,000 bytes ထက် မကျော်စေဘူး |
| `DOWNLOAD_TIMEOUT` | Default `7200` seconds |
| `FFMPEG_TIMEOUT` | Default `7200` seconds |
| `TELEGRAM_UPLOAD_TIMEOUT` | Default `7200` seconds |
| `PROGRESS_EDIT_INTERVAL` | Default `3.0` seconds — Telegram progress edit throttle |
| `PROGRESS_WATCH_INTERVAL` | Default `5` seconds — watchdog polling interval |
| `PROGRESS_STALL_SECONDS` | Default `60` seconds — watchdog check နဲ့ FFmpeg stats/CPU နှစ်ခုလုံး ရပ်နေတဲ့ render stall detection |
| `JOB_MAX_ATTEMPTS` | Default `3` — transient stage failure အတွက် စုစုပေါင်း attempts |
| `JOB_RETRY_DELAY_SECONDS` | Default `5` — retry အကြား base delay; attempt အလိုက် တိုးတယ် |
| `RETRY_JOB_TTL_SECONDS` | Default `86400` — memory-only manual Retry button data သက်တမ်း |

> Render ရဲ့ default filesystem က ephemeral ဖြစ်လို့ redeploy/restart အလိုက် clone cache ပျောက်နိုင်ပါတယ်။ Persistent disk သုံးပြီး `VOICE_CLONE_CACHE_PATH` ကို ညှိတာ (သို့) ElevenLabs dashboard က ရထားတဲ့ cloned ID ကို `ELEVENLABS_VOICE_ID` မှာ ထည့်တာက clone အသစ်တွေ ထပ်မဆောက်မိအောင် ကူညီနိုင်ပါတယ်။

**API keys တွေကို `bot.py`၊ README သို့မဟုတ် Git repository ထဲ မထည့်ပါနဲ့။ Render Environment Variables / Secrets ထဲမှာသာ ထည့်ပါ။**

Health check path က `/health` ဖြစ်ပြီး service စတင်ပြီးသွားရင် Telegram webhook ကို `/webhook` ဆီ အလိုအလျောက် ချိတ်ပေးပါတယ်။

## Tests

API call မလုပ်ဘဲ script prompt၊ four-way gapless ranges၊ shared story map/audio reference၊ worker retry/full-story fallback၊ 80% timing fit၊ Voice-01/provider lock နဲ့ ordered audio mixing၊ four-part common encoding settings/ordered join/stream validation/sequential fallback၊ Gemini slow-helper race/cancellation၊ DeepSeek timeout fallback၊ JSON parsing၊ PCM handling၊ voice-clone multipart/cache/provider priority၊ Telegram clone command၊ inline Voice Lab buttons၊ Voice Test audio upload၊ MB progress card၊ 60s watchdog၊ automatic/manual retry၊ DYY always-on/start guide/context-aware next steps/event isolation၊ source-credit/rights captions၊ target-size encoding correction၊ real subprocess FFmpeg-progress stream/stall cleanup နဲ့ Telegram splitting logic ကို စမ်းနိုင်ပါတယ်။ Media/provider tests အများကြီးက mocks/local files ကို သုံးထားတဲ့အတွက် ဒီ suite အောင်တာက live Gemini/ElevenLabs/FFmpeg/Telegram end-to-end production validation ကို အစားမထိုးပါဘူး။

```bash
python -m unittest discover -s tests -v
```
