import asyncio
import json
import os
import sys
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import bot


class RecapPromptTests(unittest.TestCase):
    def test_prompt_uses_first_person_throughout(self):
        prompt = bot.build_recap_script_prompt(42)
        self.assertIn("FIRST-PERSON point of view", prompt)
        self.assertIn("NEVER switch to an outside or third-person narrator", prompt)
        self.assertIn("same first-person voice", prompt)
        self.assertIn("ကျွန်တော်", prompt)
        self.assertIn("ကျွန်မ", prompt)
        self.assertIn("NOT a line-by-line translation", prompt)
        self.assertIn("42.0-second audio clip", prompt)
        self.assertIn("33.6 seconds", prompt)
        self.assertIn("80% of the source video", prompt)

        example_text = prompt.split(
            "Return ONLY valid JSON matching this exact structure (no markdown fences):",
            1,
        )[1]
        example = json.loads(example_text)
        self.assertEqual(example[0]["gender"], "male")
        self.assertEqual(example[0]["end"], 6.5)
        self.assertTrue(example[0]["burmese"].startswith("ကျွန်တော်"))
        self.assertNotIn("သူ့စိတ်ထဲ", example[0]["burmese"])

    def test_json_payload_accepts_fences_and_surrounding_text(self):
        payload = bot.parse_json_payload('Result:\n```json\n[{"start": 0, "end": 2}]\n```')
        self.assertEqual(payload, [{"start": 0, "end": 2}])

    def test_narration_windows_fit_budget_without_cutting_timeline(self):
        segments = [
            {"start": 0.0, "end": 50.0, "gender": "male", "burmese": "ပထမအပိုင်း"},
            {"start": 50.0, "end": 100.0, "gender": "male", "burmese": "ဒုတိယအပိုင်း"},
        ]
        fitted = bot.fit_recap_narration_windows(segments, 100.0)
        allocated = sum(item["end"] - item["start"] for item in fitted)
        self.assertAlmostEqual(allocated, 80.0, delta=0.001)
        self.assertGreater(fitted[0]["start"], 0.0)
        self.assertLess(fitted[-1]["end"], 100.0)
        self.assertGreaterEqual(fitted[1]["start"], fitted[0]["end"])
        self.assertEqual(fitted[1]["burmese"], "ဒုတိယအပိုင်း")


class MediaSizingTests(unittest.TestCase):
    def test_encoding_bitrates_fit_target_size(self):
        target = 800 * 1024 * 1024
        duration = 3600.0
        video_bps, audio_bps = bot.calculate_encoding_bitrates(target, duration)
        self.assertGreater(video_bps, 0)
        self.assertGreaterEqual(audio_bps, 32_000)
        estimated_target = (video_bps + audio_bps) * duration / 8 / 0.965
        self.assertAlmostEqual(estimated_target / target, 1.0, delta=0.01)

    def test_impossible_target_is_rejected(self):
        with self.assertRaises(ValueError):
            bot.calculate_encoding_bitrates(1000, 3600.0)

    def test_human_file_size(self):
        self.assertEqual(bot.human_file_size(1024**3), "1.00 GB")
        self.assertEqual(bot.human_file_size(1024**2), "1.0 MB")

    def test_default_output_ratio_and_telegram_limit(self):
        self.assertEqual(bot.FINAL_SIZE_RATIO, 0.8)
        self.assertEqual(bot.SCRIPT_DURATION_RATIO, 0.8)
        self.assertLessEqual(bot.TELEGRAM_PART_LIMIT_BYTES, 48_000_000)


class VoiceAndAudioTests(unittest.TestCase):
    def test_elevenlabs_exact_voice_name_wins(self):
        voices = [
            {"voice_id": "deep", "name": "Deep Narrator", "labels": {"gender": "male"}},
            {"voice_id": "adam", "name": "Adam", "labels": {"gender": "male"}},
        ]
        self.assertEqual(bot.choose_elevenlabs_voice(voices, "Adam"), "adam")

    def test_elevenlabs_fallback_prefers_deep_male(self):
        voices = [
            {"voice_id": "female", "name": "Soft", "labels": {"gender": "female"}},
            {
                "voice_id": "male",
                "name": "Narrator",
                "description": "Deep low male narration",
                "labels": {"gender": "male"},
            },
        ]
        self.assertEqual(bot.choose_elevenlabs_voice(voices, "Charon"), "male")

    def test_int16_pcm_pitch_matches_normalized_float(self):
        sample_rate = 8000
        times = np.arange(sample_rate, dtype=np.float32) / sample_rate
        normalized = np.sin(2 * np.pi * 150 * times).astype(np.float32) * 0.5
        pcm = (normalized * 32767).astype(np.int16)
        float_pitch = bot.estimate_segment_pitch_hz(normalized, 0.0, 1.0)
        pcm_pitch = bot.estimate_segment_pitch_hz(pcm, 0.0, 1.0)
        self.assertAlmostEqual(float_pitch, pcm_pitch, delta=1.0)

    def test_health_check_does_not_call_external_services(self):
        self.assertEqual(asyncio.run(bot.health_check()), {"ok": True})


class VoiceCloneTests(unittest.TestCase):
    def test_create_clone_uses_official_multipart_endpoint(self):
        captured = {}

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"voice_id": "cloned-test-voice", "requires_verification": False}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, data=None, files=None):
                captured.update(
                    {"url": url, "headers": headers, "data": data, "files": files}
                )
                return FakeResponse()

        with tempfile.TemporaryDirectory() as directory, patch.object(
            bot.httpx, "AsyncClient", FakeClient
        ), patch.object(bot, "ELEVENLABS_API_KEY", "test-only"):
            reference = os.path.join(directory, "reference.wav")
            with open(reference, "wb") as reference_file:
                reference_file.write(b"a" * 2048)
            voice_id = asyncio.run(
                bot.create_elevenlabs_voice_clone(reference, "Voice 01 Test")
            )

        self.assertEqual(voice_id, "cloned-test-voice")
        self.assertTrue(captured["url"].endswith("/v1/voices/add"))
        self.assertEqual(captured["data"]["name"], "Voice 01 Test")
        self.assertIn("files", captured["files"])
        self.assertEqual(captured["files"]["files"][2], "audio/wav")

    def test_bundled_voice01_reference_is_clean_wav(self):
        reference = os.path.join(bot.BASE_DIR, "audio", "voice01_clone_reference.wav")
        with wave.open(reference, "rb") as audio_file:
            duration = audio_file.getnframes() / audio_file.getframerate()
            self.assertEqual(audio_file.getnchannels(), 1)
            self.assertEqual(audio_file.getsampwidth(), 2)
        self.assertGreaterEqual(duration, 60.0)
        self.assertLessEqual(duration, 180.0)

    def test_voice01_clone_is_cached_without_storing_api_key(self):
        clone_calls = []

        async def fake_clone(path, name):
            clone_calls.append((path, name))
            return "voice01-clone-id"

        async def run_flow():
            first = await bot.ensure_voice01_clone()
            second = await bot.ensure_voice01_clone()
            return first, second

        with tempfile.TemporaryDirectory() as directory:
            reference = os.path.join(directory, "voice01.mp3")
            cache = os.path.join(directory, "voice_clones.json")
            with open(reference, "wb") as reference_file:
                reference_file.write(b"b" * 2048)
            with patch.object(bot, "VOICE_CLONE_ENABLED", True), patch.object(
                bot, "ELEVENLABS_API_KEY", "test-only"
            ), patch.object(bot, "VOICE_CLONE_REFERENCE", reference), patch.object(
                bot, "VOICE_CLONE_CACHE_PATH", cache
            ), patch.object(bot, "voice01_clone_id", ""), patch.object(
                bot, "create_elevenlabs_voice_clone", side_effect=fake_clone
            ):
                first, second = asyncio.run(run_flow())

            self.assertEqual(first, "voice01-clone-id")
            self.assertEqual(second, "voice01-clone-id")
            self.assertEqual(len(clone_calls), 1)
            with open(cache, "r", encoding="utf-8") as cache_file:
                cache_text = cache_file.read()
            self.assertNotIn("test-only", cache_text)
            self.assertEqual(len(json.loads(cache_text)["voice01"]["reference_sha256"]), 64)

    def test_custom_clone_wins_over_environment_default(self):
        async def fail_default_clone():
            raise AssertionError("Default voice-01 clone should not be requested")

        with tempfile.TemporaryDirectory() as directory:
            cache = os.path.join(directory, "voice_clones.json")
            with open(cache, "w", encoding="utf-8") as cache_file:
                json.dump({"chats": {"123": {"voice_id": "custom-chat-clone"}}}, cache_file)
            with patch.object(bot, "VOICE_CLONE_ENABLED", True), patch.object(
                bot, "ELEVENLABS_API_KEY", "test-only"
            ), patch.object(bot, "ELEVENLABS_VOICE_ID", "environment-voice"), patch.object(
                bot, "VOICE_CLONE_CACHE_PATH", cache
            ), patch.object(bot, "ensure_voice01_clone", side_effect=fail_default_clone):
                selected = asyncio.run(bot.get_narration_clone_voice_id(123))
        self.assertEqual(selected, "custom-chat-clone")

    def test_charon_mode_bypasses_voice_clone(self):
        async def fail_default_clone():
            raise AssertionError("Charon mode must not request a voice clone")

        with tempfile.TemporaryDirectory() as directory:
            cache = os.path.join(directory, "voice_clones.json")
            with open(cache, "w", encoding="utf-8") as cache_file:
                json.dump({"chats": {"123": {"use_charon": True}}}, cache_file)
            with patch.object(bot, "VOICE_CLONE_ENABLED", True), patch.object(
                bot, "ELEVENLABS_API_KEY", "test-only"
            ), patch.object(bot, "VOICE_CLONE_CACHE_PATH", cache), patch.object(
                bot, "ensure_voice01_clone", side_effect=fail_default_clone
            ):
                selected = asyncio.run(bot.get_narration_clone_voice_id(123))
                label = bot.get_chat_voice_label(123)
        self.assertEqual(selected, "")
        self.assertIn("Charon", label)

    def test_clone_voice_is_tried_before_gemini(self):
        selected_voices = []

        async def fake_elevenlabs(text, out_path, voice_id=None):
            selected_voices.append(voice_id)
            with open(out_path, "wb") as output_file:
                output_file.write(b"c" * 512)
            return voice_id

        async def fail_gemini(*args):
            raise AssertionError("Gemini should not run before the selected clone")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            bot, "synthesize_elevenlabs_tts", side_effect=fake_elevenlabs
        ), patch.object(bot, "synthesize_gemini_tts", side_effect=fail_gemini), patch.object(
            bot, "TTS_PROVIDER", "auto"
        ):
            path, provider, resolved = asyncio.run(
                bot.synthesize_narration(
                    "ကျွန်တော် စမ်းနားထောင်တယ်",
                    directory,
                    0,
                    "elevenlabs",
                    "selected-clone",
                )
            )
        self.assertEqual(provider, "elevenlabs")
        self.assertEqual(resolved, "selected-clone")
        self.assertEqual(selected_voices, ["selected-clone"])
        self.assertTrue(path.endswith("seg_0_elevenlabs.audio"))


class GeminiTtsTests(unittest.TestCase):
    def test_charon_wav_response_is_saved(self):
        audio = b"RIFF" + (b"\x00" * 400)
        response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=audio))]
                    )
                )
            ]
        )
        fake_client = SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kwargs: response)
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bot.genai, "Client", return_value=fake_client
        ), patch.object(bot, "GEMINI_API_KEY", "test-only"), patch.object(
            bot, "GEMINI_TTS_MODELS", ["gemini-3.8-flash-tts"]
        ):
            output = os.path.join(directory, "voice.audio")
            succeeded = asyncio.run(bot.synthesize_gemini_tts("စမ်းသပ်ချက်", output))
            self.assertTrue(succeeded)
            with open(output, "rb") as audio_file:
                self.assertEqual(audio_file.read(), audio)

    def test_legacy_pcm_response_is_wrapped_as_wav(self):
        audio = b"\x00\x01" * 300
        response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=audio))]
                    )
                )
            ]
        )
        fake_client = SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kwargs: response)
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bot.genai, "Client", return_value=fake_client
        ), patch.object(bot, "GEMINI_API_KEY", "test-only"), patch.object(
            bot, "GEMINI_TTS_MODELS", ["gemini-2.5-flash-tts"]
        ):
            output = os.path.join(directory, "voice.audio")
            succeeded = asyncio.run(bot.synthesize_gemini_tts("စမ်းသပ်ချက်", output))
            self.assertTrue(succeeded)
            with wave.open(output, "rb") as wav_file:
                self.assertEqual(wav_file.getframerate(), bot.SAMPLE_RATE)
                self.assertEqual(wav_file.getnchannels(), 1)
                self.assertEqual(wav_file.readframes(300), audio)


class EncodingAndSplittingTests(unittest.TestCase):
    def test_target_encode_runs_corrective_pass(self):
        calls = []

        async def fake_run_ffmpeg(cmd, timeout, progress_callback=None):
            calls.append(cmd)
            output = cmd[-1]
            size = 800_000 if "attempt0" in output else 950_000
            with open(output, "wb") as output_file:
                output_file.truncate(size)
            if progress_callback:
                await progress_callback(
                    {"out_time_ms": "100000000", "total_size": str(size), "speed": "1.0x"}
                )
            return SimpleNamespace(returncode=0, stderr=b"")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            bot, "run_ffmpeg_with_progress", side_effect=fake_run_ffmpeg
        ), patch.object(bot, "get_media_duration", return_value=100.0):
            source = os.path.join(directory, "source.mp4")
            audio = os.path.join(directory, "audio.mp3")
            output = os.path.join(directory, "final.mp4")
            open(source, "wb").close()
            open(audio, "wb").close()
            final_size = asyncio.run(
                bot.encode_video_to_target_size(source, output, 1_000_000, 100.0, audio)
            )
            self.assertEqual(final_size, 950_000)
            self.assertEqual(os.path.getsize(output), 950_000)
            self.assertEqual(len(calls), 2)
            self.assertIn("-b:v", calls[0])
            self.assertIn("-progress", calls[0])
            self.assertIn("pipe:1", calls[0])

    def test_ffmpeg_machine_progress_is_streamed_from_real_subprocess(self):
        reports = []
        script = (
            "import sys;"
            "sys.stdout.write('frame=25\\nout_time_ms=5000000\\ntotal_size=12345\\n"
            "speed=1.2x\\nprogress=continue\\n');sys.stdout.flush();"
            "sys.stdout.write('frame=50\\nout_time_ms=10000000\\ntotal_size=20000\\n"
            "speed=1.1x\\nprogress=end\\n');sys.stdout.flush()"
        )

        async def report(stats):
            reports.append(stats)

        result = asyncio.run(
            bot.run_ffmpeg_with_progress(
                [sys.executable, "-u", "-c", script],
                10,
                progress_callback=report,
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["out_time_ms"], "5000000")
        self.assertEqual(reports[0]["total_size"], "12345")
        self.assertEqual(reports[1]["progress"], "end")

    def test_idle_ffmpeg_process_is_stopped_and_marked_retryable(self):
        started = bot.time.monotonic()

        async def run_flow():
            with patch.object(bot, "PROGRESS_STALL_SECONDS", 0.2), patch.object(
                bot, "PROGRESS_WATCH_INTERVAL", 0.05
            ), patch.object(bot, "process_cpu_seconds", return_value=0.0):
                await bot.run_ffmpeg_with_progress(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    10,
                )

        with self.assertRaises(bot.RetryableJobError) as raised:
            asyncio.run(run_flow())

        self.assertLess(bot.time.monotonic() - started, 5.0)
        self.assertIn("no CPU", str(raised.exception))

    def test_quiet_process_with_advancing_cpu_is_not_killed(self):
        async def run_flow():
            with patch.object(bot, "PROGRESS_STALL_SECONDS", 0.1), patch.object(
                bot, "PROGRESS_WATCH_INTERVAL", 0.05
            ), patch.object(
                bot, "process_cpu_seconds", side_effect=lambda _pid: bot.time.monotonic()
            ):
                return await bot.run_ffmpeg_with_progress(
                    [sys.executable, "-c", "import time; time.sleep(0.35)"],
                    10,
                )

        result = asyncio.run(run_flow())
        self.assertEqual(result.returncode, 0)

    def test_large_file_is_split_in_order_under_limit(self):
        def fake_run_cmd(cmd, timeout):
            pattern = cmd[-1]
            for index in range(3):
                with open(pattern % index, "wb") as output_file:
                    output_file.truncate(4000)
            return SimpleNamespace(returncode=0, stderr=b"")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            bot, "run_cmd", side_effect=fake_run_cmd
        ), patch.object(bot, "get_media_duration", return_value=100.0), patch.object(
            bot, "TELEGRAM_PART_LIMIT_BYTES", 5000
        ):
            final = os.path.join(directory, "final.mp4")
            with open(final, "wb") as final_file:
                final_file.truncate(12_000)
            parts = asyncio.run(bot.split_video_for_telegram(final, directory))
            self.assertEqual(len(parts), 3)
            self.assertTrue(all(os.path.getsize(part) <= 5000 for part in parts))
            self.assertEqual(
                [os.path.basename(part) for part in parts],
                ["part_0001.mp4", "part_0002.mp4", "part_0003.mp4"],
            )


class ProgressAndRetryTests(unittest.TestCase):
    def test_progress_card_shows_mb_and_percentage(self):
        progress = bot.JobProgress(123, 456, "job123")

        async def fake_edit_text(*args, **kwargs):
            return None

        with patch.object(bot, "edit_text", side_effect=fake_edit_text):
            asyncio.run(
                progress.set_stage(
                    "download",
                    "Downloading test video",
                    current=50_000_000,
                    total=100_000_000,
                )
            )
        card = progress.render()
        self.assertIn("50.0 MB / 100.0 MB", card)
        self.assertIn("50%", card)
        self.assertIn("Download", card)
        self.assertIn("60-second watchdog", card)

    def test_watchdog_checks_after_sixty_seconds_without_failing_job(self):
        progress = bot.JobProgress(123, 456, "job123")
        progress.last_activity = bot.time.monotonic() - 61

        async def fake_edit_text(*args, **kwargs):
            return None

        with patch.object(bot, "edit_text", side_effect=fake_edit_text):
            triggered = asyncio.run(progress.check_watchdog())
        self.assertTrue(triggered)
        self.assertEqual(progress.watchdog_checks, 1)
        self.assertIn("Watchdog check", progress.notice)
        self.assertEqual(progress.error, "")
        self.assertFalse(progress.finished)

    def test_transient_stage_failure_is_retried(self):
        progress = bot.JobProgress(123, 456, "job123")
        attempts = []

        async def operation():
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise bot.RetryableJobError("network timeout")
            return "ok"

        async def fake_edit_text(*args, **kwargs):
            return None

        with patch.object(bot, "edit_text", side_effect=fake_edit_text), patch.object(
            bot, "JOB_RETRY_DELAY_SECONDS", 0
        ):
            result = asyncio.run(bot.run_with_retries(operation, progress, "download"))
        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(progress.attempt, 2)

    def test_permanent_stage_failure_is_not_retried(self):
        progress = bot.JobProgress(123, 456, "job123")
        attempts = []

        async def operation():
            attempts.append(1)
            raise bot.PermanentJobError("No audio")

        with self.assertRaises(bot.PermanentJobError):
            asyncio.run(bot.run_with_retries(operation, progress, "script"))
        self.assertEqual(len(attempts), 1)

    def test_retry_callback_restarts_saved_job(self):
        bot.retry_jobs["retry123"] = {
            "chat_id": 123,
            "reply_msg_id": 456,
            "url": "https://example.com/video",
            "created": bot.time.time(),
        }
        update = {
            "update_id": 990010,
            "callback_query": {
                "id": "retry-callback",
                "data": "retry:retry123",
                "message": {"chat": {"id": 123}, "message_id": 999},
            },
        }

        class FakeRequest:
            async def json(self):
                return update

        scheduled = []
        restarted = []

        async def fake_tg_api(*args, **kwargs):
            return {"ok": True}

        async def fake_handle(chat_id, reply_id, url, job_id=None):
            restarted.append((chat_id, reply_id, url, job_id))

        async def run_flow():
            with patch.object(bot, "tg_api", side_effect=fake_tg_api), patch.object(
                bot, "handle_video_job", side_effect=fake_handle
            ), patch.object(
                bot, "schedule_background", side_effect=lambda coroutine: scheduled.append(coroutine)
            ):
                await bot.telegram_webhook(FakeRequest())
                for coroutine in scheduled:
                    await coroutine

        try:
            asyncio.run(run_flow())
        finally:
            bot.retry_jobs.pop("retry123", None)
        self.assertEqual(
            restarted,
            [(123, 456, "https://example.com/video", "retry123")],
        )
    def test_failed_job_offers_manual_retry_button(self):
        edits = []

        async def fake_download(*args):
            return False

        async def fake_send_text(*args, **kwargs):
            return 99

        async def fake_edit_text(chat_id, message_id, text, reply_markup=None):
            edits.append((text, reply_markup))

        with patch.object(bot, "download_rednote_video", side_effect=fake_download), patch.object(
            bot, "send_text", side_effect=fake_send_text
        ), patch.object(bot, "edit_text", side_effect=fake_edit_text), patch.object(
            bot, "JOB_MAX_ATTEMPTS", 1
        ):
            try:
                asyncio.run(
                    bot.handle_video_job(
                        123,
                        456,
                        "https://example.com/video",
                        job_id="failed123",
                    )
                )
                self.assertIn("failed123", bot.retry_jobs)
                retry_markups = [markup for _, markup in edits if markup]
                self.assertTrue(retry_markups)
                self.assertEqual(
                    retry_markups[-1]["inline_keyboard"][0][0]["callback_data"],
                    "retry:failed123",
                )
            finally:
                bot.retry_jobs.pop("failed123", None)


class DYYAgentTests(unittest.TestCase):
    def test_dyy_report_addresses_user_and_escapes_job_details(self):
        sent = []

        async def fake_send_text(chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return 88

        event = {
            "event": "retry",
            "job_id": "dyy-job",
            "stage": "download",
            "detail": "Temporary <network> failure",
            "current": 2_000_000,
            "total": 8_000_000,
            "attempt": 2,
        }
        with patch.object(bot, "send_text", side_effect=fake_send_text):
            asyncio.run(bot.DYYAgent(123).report(event))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 123)
        self.assertIn("ဆရာ", sent[0][1])
        self.assertIn("DYY AGENT", sent[0][1])
        self.assertIn("dyy-job", sent[0][1])
        self.assertIn("Temporary &lt;network&gt; failure", sent[0][1])
        self.assertIn("Data:", sent[0][1])
        self.assertIn("Attempt:</b> 2", sent[0][1])

    def test_dyy_start_is_always_on_usage_guide(self):
        update = {
            "update_id": 990011,
            "message": {
                "message_id": 11,
                "chat": {"id": 123},
                "text": "/start",
            },
        }

        class FakeRequest:
            async def json(self):
                return update

        sent = []
        scheduled = []

        async def fake_send_text(chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return 88

        async def run_flow():
            with patch.object(bot, "send_text", side_effect=fake_send_text), patch.object(
                bot, "schedule_background", side_effect=lambda coroutine: scheduled.append(coroutine)
            ):
                await bot.telegram_webhook(FakeRequest())
                for coroutine in scheduled:
                    await coroutine

        asyncio.run(run_flow())

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 123)
        self.assertIn("DYY AGENT", sent[0][1])
        self.assertIn("ALWAYS ON", sent[0][1])
        self.assertIn("ဆရာ အသုံးပြုရမယ့်ပုံ", sent[0][1])
        self.assertIn("Share → Copy Link", sent[0][1])
        self.assertNotIn("ON/OFF", sent[0][1])
        self.assertEqual(sent[0][2]["reply_to"], 11)

    def test_dyy_next_action_is_context_aware(self):
        queued = bot.dyy_next_action({"event": "queued"})
        no_audio = bot.dyy_next_action(
            {"event": "fail", "detail": "No usable speech audio was found"}
        )
        api_key = bot.dyy_next_action(
            {"event": "fail", "detail": "No API key was provided"}
        )
        retry = bot.dyy_next_action(
            {"event": "fail", "detail": "Unknown failure", "retry_available": True}
        )
        permanent_config = bot.dyy_next_action(
            {"event": "fail", "detail": "No API key", "retry_available": False}
        )

        self.assertIn("ဘာမှထပ်ပို့စရာမလို", queued)
        self.assertIn("အသံပါတဲ့", no_audio)
        self.assertIn("Render environment", api_key)
        self.assertIn("Retry Job", retry)
        self.assertIn("မူရင်း link", permanent_config)
        self.assertNotIn("Retry Job", permanent_config)

    def test_progress_emits_meaningful_events_but_not_frequent_mb_updates(self):
        events = []

        async def record(event):
            events.append(event)

        async def fake_edit_text(*args, **kwargs):
            return None

        async def run_flow():
            progress = bot.JobProgress(123, 456, "events123", event_callback=record)
            await progress.set_stage("download", "Starting", current=0, total=1000)
            await progress.update(detail="Frequent MB update", current=500)
            await progress.complete("download", "Download complete")
            await progress.succeed("Delivered")

        with patch.object(bot, "edit_text", side_effect=fake_edit_text):
            asyncio.run(run_flow())

        self.assertEqual([event["event"] for event in events], ["stage", "complete", "success"])
        self.assertTrue(all(event["job_id"] == "events123" for event in events))
        self.assertNotIn("Frequent MB update", [event["detail"] for event in events])

    def test_watchdog_and_retry_events_reach_dyy(self):
        events = []

        async def record(event):
            events.append(event)

        async def fake_edit_text(*args, **kwargs):
            return None

        attempts = []

        async def operation():
            attempts.append(1)
            if len(attempts) == 1:
                raise bot.RetryableJobError("network timeout")
            return "ok"

        async def run_flow():
            progress = bot.JobProgress(123, 456, "watch123", event_callback=record)
            progress.last_activity -= bot.PROGRESS_STALL_SECONDS + 1
            await progress.check_watchdog()
            await bot.run_with_retries(operation, progress, "download")

        with patch.object(bot, "edit_text", side_effect=fake_edit_text), patch.object(
            bot, "JOB_RETRY_DELAY_SECONDS", 0
        ):
            asyncio.run(run_flow())

        self.assertEqual([event["event"] for event in events], ["watchdog", "retry"])
        self.assertEqual(events[1]["attempt"], 1)
        self.assertEqual(len(attempts), 2)

    def test_monitoring_failure_cannot_interrupt_progress(self):
        async def broken_report(event):
            raise RuntimeError("Telegram monitoring failed")

        async def fake_edit_text(*args, **kwargs):
            return None

        progress = bot.JobProgress(123, 456, "safe123", event_callback=broken_report)
        with patch.object(bot, "edit_text", side_effect=fake_edit_text):
            asyncio.run(progress.set_stage("download", "Job continues"))
        self.assertEqual(progress.stage, "download")
        self.assertEqual(progress.detail, "Job continues")


class CopyrightCaptionTests(unittest.TestCase):
    def test_first_part_has_source_attribution_and_rights_notice(self):
        with patch.object(bot, "SOURCE_CREDIT_ENABLED", True), patch.object(
            bot, "COPYRIGHT_NOTICE_ENABLED", True
        ):
            first = bot.copyright_caption("https://example.com/watch?a=1&b=2", 1)
            second = bot.copyright_caption("https://example.com/watch?a=1&b=2", 2)

        self.assertIn('<a href="https://example.com/watch?a=1&amp;b=2">', first)
        self.assertIn("မူရင်းပိုင်ဆိုင်မှုများ", first)
        self.assertNotIn("100%", first)
        self.assertNotIn("shield", first.lower())
        self.assertEqual(second, "")

    def test_copyright_caption_can_be_disabled(self):
        with patch.object(bot, "SOURCE_CREDIT_ENABLED", False), patch.object(
            bot, "COPYRIGHT_NOTICE_ENABLED", False
        ):
            caption = bot.copyright_caption("https://example.com/video", 1)
        self.assertEqual(caption, "")


class BotInterfaceTests(unittest.TestCase):
    def test_menu_templates_and_keyboard(self):
        welcome = bot.welcome_message()
        keyboard = bot.bot_menu_keyboard()
        self.assertIn("<b>🎬 REDNOTE MM RECAP</b>", welcome)
        self.assertIn("First-person", welcome)
        callbacks = [
            button["callback_data"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        for action in (
            "new_recap",
            "clone_voice",
            "voice01",
            "voice_test",
            "voice_lab",
            "help",
            "status",
        ):
            self.assertIn(action, callbacks)
        lab_callbacks = [
            button["callback_data"]
            for row in bot.voice_lab_keyboard()["inline_keyboard"]
            for button in row
        ]
        self.assertIn("usecharon", lab_callbacks)
        self.assertIn("home", lab_callbacks)
        self.assertIn("/status", bot.help_message())
        self.assertIn("/voicetest", bot.help_message())
        self.assertNotIn("/dyy", bot.help_message())
        self.assertNotIn("dyy", callbacks)
        self.assertIn("Always On · Live", bot.status_message(123))
        self.assertFalse(hasattr(bot, "dyy_preferences"))
        self.assertFalse(hasattr(bot, "DYY_AGENT_ENABLED"))

    def test_voice_clone_sample_detection_rejects_non_audio_documents(self):
        voice = bot.find_telegram_voice_sample(
            {"voice": {"file_id": "voice-file", "file_size": 2048}}
        )
        self.assertEqual(voice["file_id"], "voice-file")
        self.assertEqual(
            bot.find_telegram_voice_sample(
                {"document": {"file_id": "pdf-file", "mime_type": "application/pdf"}}
            ),
            {},
        )

    def test_clonevoice_caption_schedules_clone_job(self):
        update = {
            "update_id": 990002,
            "message": {
                "message_id": 11,
                "chat": {"id": 123},
                "caption": "/clonevoice",
                "voice": {"file_id": "voice-file", "file_size": 2048},
            },
        }

        class FakeRequest:
            async def json(self):
                return update

        scheduled = []
        cloned = []

        async def fake_clone_job(chat_id, message_id, sample):
            cloned.append((chat_id, message_id, sample["file_id"]))

        async def run_flow():
            with patch.object(
                bot, "handle_voice_clone_job", side_effect=fake_clone_job
            ), patch.object(
                bot, "schedule_background", side_effect=lambda coroutine: scheduled.append(coroutine)
            ):
                await bot.telegram_webhook(FakeRequest())
                for coroutine in scheduled:
                    await coroutine

        asyncio.run(run_flow())
        self.assertEqual(cloned, [(123, 11, "voice-file")])

    def test_send_text_uses_html_and_keyboard(self):
        calls = []

        async def fake_tg_api(method, data=None, files=None, timeout=120.0):
            calls.append((method, data))
            return {"ok": True, "result": {"message_id": 77}}

        with patch.object(bot, "tg_api", side_effect=fake_tg_api):
            message_id = asyncio.run(
                bot.send_text(123, "<b>Hello</b>", reply_markup=bot.bot_menu_keyboard())
            )
        self.assertEqual(message_id, 77)
        payload = calls[0][1]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertIn("inline_keyboard", payload["reply_markup"])

    def test_status_page_is_responsive_and_contains_webhook(self):
        request = SimpleNamespace(base_url="https://example.com/")
        with patch.object(bot, "BOT_TOKEN", ""):
            response = asyncio.run(bot.index(request))
        page = response.body.decode("utf-8")
        self.assertIn('name="viewport"', page)
        self.assertIn("Rednote MM Recap", page)
        self.assertIn("First-person", page)
        self.assertIn("https://example.com/webhook", page)
        self.assertNotIn("__WH_STATUS__", page)

    def test_help_inline_button_answers_callback_and_sends_card(self):
        update = {
            "update_id": 990001,
            "callback_query": {
                "id": "callback-1",
                "data": "help",
                "message": {"chat": {"id": 123}, "message_id": 10},
            },
        }

        class FakeRequest:
            async def json(self):
                return update

        api_calls = []
        sent = []
        scheduled = []

        async def fake_tg_api(method, data=None, files=None, timeout=120.0):
            api_calls.append((method, data))
            return {"ok": True}

        async def fake_send_text(chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return 88

        def fake_schedule(coroutine):
            scheduled.append(coroutine)

        async def run_flow():
            with patch.object(bot, "tg_api", side_effect=fake_tg_api), patch.object(
                bot, "send_text", side_effect=fake_send_text
            ), patch.object(bot, "schedule_background", side_effect=fake_schedule):
                await bot.telegram_webhook(FakeRequest())
                for coroutine in scheduled:
                    await coroutine

        asyncio.run(run_flow())
        self.assertEqual(api_calls[0][0], "answerCallbackQuery")
        self.assertEqual(sent[0][0], 123)
        self.assertIn("အသုံးပြုပုံ", sent[0][1])
        self.assertIn("reply_markup", sent[0][2])
    def test_clone_voice_inline_button_sends_working_guide(self):
        update = {
            "update_id": 990003,
            "callback_query": {
                "id": "callback-clone",
                "data": "clone_voice",
                "message": {"chat": {"id": 123}, "message_id": 12},
            },
        }

        class FakeRequest:
            async def json(self):
                return update

        sent = []
        scheduled = []

        async def fake_tg_api(method, data=None, files=None, timeout=120.0):
            return {"ok": True}

        async def fake_send_text(chat_id, text, **kwargs):
            sent.append((chat_id, text, kwargs))
            return 88

        async def run_flow():
            with patch.object(bot, "tg_api", side_effect=fake_tg_api), patch.object(
                bot, "send_text", side_effect=fake_send_text
            ), patch.object(
                bot, "schedule_background", side_effect=lambda coroutine: scheduled.append(coroutine)
            ):
                await bot.telegram_webhook(FakeRequest())
                for coroutine in scheduled:
                    await coroutine

        asyncio.run(run_flow())
        self.assertEqual(sent[0][0], 123)
        self.assertIn("VOICE CLONE", sent[0][1])
        self.assertIn("/clonevoice", sent[0][1])
        self.assertEqual(
            sent[0][2]["reply_markup"]["inline_keyboard"][0][1]["callback_data"],
            "voice_test",
        )


class VoicePreviewTests(unittest.TestCase):
    def test_voice_preview_uses_selected_clone_and_uploads_audio(self):
        synthesis_calls = []
        uploads = []

        async def fake_synthesize(text, work_dir, index, preferred_provider, voice_id):
            synthesis_calls.append((preferred_provider, voice_id))
            raw_path = os.path.join(work_dir, "raw_preview.mp3")
            with open(raw_path, "wb") as raw_file:
                raw_file.write(b"raw-audio")
            return raw_path, "elevenlabs", voice_id

        def fake_run_cmd(cmd, timeout):
            with open(cmd[-1], "wb") as output_file:
                output_file.write(b"converted-audio")
            return SimpleNamespace(returncode=0, stderr=b"")

        async def fake_send_text(*args, **kwargs):
            return 99

        async def fake_edit_text(*args, **kwargs):
            return None

        async def fake_tg_api(method, data=None, files=None, timeout=120.0):
            if method == "sendAudio":
                uploads.append((data, files))
            return {"ok": True}

        with patch.object(
            bot, "get_narration_clone_voice_id", return_value="selected-clone"
        ), patch.object(
            bot, "synthesize_narration", side_effect=fake_synthesize
        ), patch.object(bot, "run_cmd", side_effect=fake_run_cmd), patch.object(
            bot, "send_text", side_effect=fake_send_text
        ), patch.object(bot, "edit_text", side_effect=fake_edit_text), patch.object(
            bot, "tg_api", side_effect=fake_tg_api
        ):
            asyncio.run(bot.handle_voice_preview(123, 456))

        self.assertEqual(synthesis_calls, [("elevenlabs", "selected-clone")])
        self.assertEqual(len(uploads), 1)
        self.assertIn("Selected voice clone", uploads[0][0]["caption"])
        self.assertEqual(uploads[0][1]["audio"][2], "audio/mpeg")


class HandlerFlowTests(unittest.TestCase):
    def test_large_video_handler_reaches_telegram_upload(self):
        sent_videos = []
        edits = []
        dyy_messages = []
        work_directories = []

        async def fake_download(url, output):
            work_directories.append(os.path.dirname(output))
            with open(output, "wb") as source_file:
                source_file.truncate(10_000)
            return True

        def fake_run_cmd(cmd, timeout):
            for value in cmd:
                if not isinstance(value, str):
                    continue
                if value.endswith("speech.mp3"):
                    with open(value, "wb") as audio_file:
                        audio_file.write(b"audio")
                elif value.endswith("speech8k.pcm"):
                    with open(value, "wb") as pcm_file:
                        pcm_file.truncate(32_000)
            return SimpleNamespace(returncode=0, stderr=b"")

        async def fake_script(*args):
            return [{"start": 0.0, "end": 3.0, "gender": "male", "burmese": "ကျွန်တော် ပြောလိုက်တယ်"}]

        async def fake_synthesize(
            segments, duration, work_dir, output, clone_voice_id=None, progress_callback=None
        ):
            with open(output, "wb") as audio_file:
                audio_file.write(b"dub")
            if progress_callback:
                await progress_callback(len(segments), len(segments), len(segments))
            return "gemini"

        async def fake_encode(
            source, output, target, duration, audio_path=None, progress_callback=None
        ):
            if progress_callback:
                await progress_callback(
                    {
                        "out_time_ms": str(int(duration * 500_000)),
                        "total_size": str(target // 2),
                        "speed": "1.5x",
                    },
                    1,
                )
            with open(output, "wb") as final_file:
                final_file.truncate(target)
            return target

        async def fake_split(final, work_dir):
            return [final]

        async def fake_send_text(chat_id, text, *args, **kwargs):
            if "DYY AGENT" in text:
                dyy_messages.append(text)
            return 99

        async def fake_edit_text(chat_id, message_id, text, reply_markup=None):
            edits.append(text)

        async def fake_tg_api(method, data=None, files=None, timeout=120.0):
            if method == "sendVideo":
                sent_videos.append(data)
                self.assertIsNotNone(files)
            return {"ok": True, "result": {"message_id": 100}}

        with patch.object(
            bot, "download_rednote_video", side_effect=fake_download
        ), patch.object(
            bot, "run_cmd", side_effect=fake_run_cmd
        ), patch.object(bot, "get_media_duration", return_value=10.0), patch.object(
            bot, "create_recap_script_gemini", side_effect=fake_script
        ), patch.object(
            bot, "get_narration_clone_voice_id", return_value=""
        ), patch.object(
            bot, "synthesize_dubbed_track", side_effect=fake_synthesize
        ), patch.object(
            bot, "encode_video_to_target_size", side_effect=fake_encode
        ), patch.object(bot, "split_video_for_telegram", side_effect=fake_split), patch.object(
            bot, "send_text", side_effect=fake_send_text
        ), patch.object(bot, "edit_text", side_effect=fake_edit_text), patch.object(
            bot, "tg_api", side_effect=fake_tg_api
        ), patch.object(bot, "PROGRESS_EDIT_INTERVAL", 0):
            asyncio.run(bot.handle_video_job(123, 456, "https://example.com/video"))

        self.assertEqual(len(sent_videos), 1)
        self.assertIn("80%", sent_videos[0]["caption"])
        self.assertIn('href="https://example.com/video"', sent_videos[0]["caption"])
        self.assertIn("မူရင်းပိုင်ဆိုင်မှုများ", sent_videos[0]["caption"])
        self.assertTrue(any("လက်ခံရရှိ" in text for text in dyy_messages))
        self.assertTrue(any("Recap အပြည့်အစုံ" in text for text in dyy_messages))
        self.assertTrue(all("ဆရာ" in text for text in dyy_messages))
        self.assertTrue(all("Always On" in text for text in dyy_messages))
        self.assertTrue(any("first-person" in text.lower() for text in edits))
        self.assertTrue(any("50% timeline" in text and "1.5x" in text for text in edits))
        self.assertTrue(work_directories)
        self.assertFalse(os.path.exists(work_directories[0]))


if __name__ == "__main__":
    unittest.main()
