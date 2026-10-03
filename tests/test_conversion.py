import asyncio
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
import zlib

from fastapi import BackgroundTasks, HTTPException, UploadFile
from starlette.requests import Request

import main


def png_fixture():
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)

    row = b'\x00' + b'\xff\x00\x00' * 2
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * 2)) + chunk(b'IEND', b''))


def upload():
    data = png_fixture()
    return UploadFile(filename='input.png', file=io.BytesIO(data), size=len(data))


class EncoderCommandTests(unittest.TestCase):
    def test_avif_lossless_speed_and_threads(self):
        with patch.object(main, 'ENCODER_THREADS', 2):
            for setting, speed in ((0, 0), (50, 4), (100, 8)):
                command = main._heif_encoder_command('avif', 'lossless', setting, 'input.png', 'output.avif')
                self.assertIn('--lossless', command)
                self.assertIn('threads=2', command)
                self.assertIn(f'speed={speed}', command)
                self.assertIn('aom', command)
                self.assertIn('--avif', command)
                self.assertEqual(command[-3:], ['--output', 'output.avif', 'input.png'])

    def test_avif_lossy_keeps_quality_separate_from_speed(self):
        command = main._heif_encoder_command('avif', 'lossy', 80, 'input.png', 'output.avif')
        self.assertIn('speed=6', command)
        self.assertEqual(command[command.index('--quality') + 1], '80')
        self.assertNotIn('--lossless', command)

    def test_heif_presets_and_thread_controls(self):
        with patch.object(main, 'ENCODER_THREADS', 1):
            for setting, preset in ((0, 'veryslow'), (50, 'fast'), (100, 'ultrafast')):
                command = main._heif_encoder_command('heif', 'lossless', setting, 'input.png', 'output.heif')
                self.assertIn('x265', command)
                self.assertIn(f'preset={preset}', command)
                self.assertIn('x265:pools=1', command)
                self.assertIn('x265:frame-threads=1', command)
                self.assertIn('--lossless', command)
                self.assertNotIn('--avif', command)

    def test_heif_lossy_quality(self):
        command = main._heif_encoder_command('heif', 'lossy', 75, 'input.png', 'output.heif')
        self.assertIn('preset=medium', command)
        self.assertEqual(command[command.index('--quality') + 1], '75')


class AsyncConversionTests(unittest.IsolatedAsyncioTestCase):
    async def test_homepage(self):
        response = await main.root(Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []}))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn(b'<form', response.body)
        self.assertIn(b'value="75"', response.body)

    async def test_form_default_is_fast_lossless(self):
        spec = main.app.openapi()
        reference = spec['paths']['/']['post']['requestBody']['content']['multipart/form-data']['schema']['$ref']
        schema = spec['components']['schemas'][reference.rsplit('/', 1)[1]]
        self.assertEqual(schema['properties']['setting']['default'], 75)
        self.assertEqual(schema['properties']['mode']['default'], 'lossless')

    async def test_upload_validation_preserves_position(self):
        file = upload()
        file.file.seek(8)
        self.assertTrue(await main.validate_image_content(file))
        self.assertEqual(file.file.tell(), 8)
        self.assertEqual(await main.get_upload_file_size(file), len(png_fixture()))
        self.assertEqual(file.file.tell(), 8)
        await file.close()

    async def test_upload_size_fallback_preserves_position(self):
        file = upload()
        file.size = None
        file.file.seek(4)
        self.assertEqual(await main.get_upload_file_size(file), len(png_fixture()))
        self.assertEqual(file.file.tell(), 4)
        await file.close()

    async def test_copy_runs_off_event_loop(self):
        file = upload()
        started = threading.Event()
        release = threading.Event()
        thread_ids = []
        original = main.shutil.copyfileobj

        def slow_copy(source, destination, length):
            thread_ids.append(threading.get_ident())
            started.set()
            if not release.wait(2):
                raise RuntimeError('copy was not released by event loop')
            original(source, destination, length)

        with tempfile.TemporaryDirectory() as directory, patch.object(main.shutil, 'copyfileobj', slow_copy):
            path = Path(directory) / 'input.png'
            task = asyncio.create_task(main._save_upload(file, str(path)))
            try:
                await asyncio.wait_for(asyncio.to_thread(started.wait), 1)
                self.assertNotEqual(thread_ids[0], threading.get_ident())
            finally:
                release.set()
                await task
            self.assertEqual(path.read_bytes(), png_fixture())
        await file.close()

    async def test_cancelled_copy_finishes_before_cleanup(self):
        file = upload()
        started = threading.Event()
        release = threading.Event()
        original = main.shutil.copyfileobj

        def slow_copy(source, destination, length):
            started.set()
            release.wait(2)
            original(source, destination, length)

        with tempfile.TemporaryDirectory() as directory, patch.object(main.shutil, 'copyfileobj', slow_copy):
            path = Path(directory) / 'input.png'
            task = asyncio.create_task(main._save_upload(file, str(path)))
            await asyncio.wait_for(asyncio.to_thread(started.wait), 1)
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(path.read_bytes(), png_fixture())
        await file.close()

    async def test_concurrency_limits_copy_and_conversion(self):
        active = 0
        peak = 0

        async def fake_copy(file, path):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)

        async def fake_command(command):
            nonlocal active
            await asyncio.sleep(0.01)
            active -= 1

        for limit in (1, 2):
            active = peak = 0
            with patch.object(main, 'conversion_semaphore', asyncio.Semaphore(limit)), \
                    patch.object(main, '_save_upload', fake_copy), \
                    patch.object(main, '_run_conversion_command', fake_command):
                await asyncio.gather(*(main._convert_with_limit(None, 'input.png', [['test']]) for _ in range(4)))
            self.assertEqual(peak, limit)
            self.assertEqual(active, 0)

    async def test_timeout_includes_queue_and_releases_upload(self):
        file = upload()
        semaphore = asyncio.Semaphore(0)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'TEMP_DIR', directory), \
                patch.object(main, 'TIMEOUT_SECONDS', 0.03), \
                patch.object(main, 'conversion_semaphore', semaphore), \
                patch.object(main, '_save_upload', AsyncMock()) as copy:
            with self.assertRaises(HTTPException) as raised:
                await main._perform_conversion(BackgroundTasks(), file, 'webp', 'lossy', 80)
            self.assertEqual(raised.exception.status_code, 504)
            copy.assert_not_awaited()
            self.assertTrue(file.file.closed)
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_conversion_stages_share_one_deadline(self):
        file = upload()
        commands = []

        async def slow_command(command):
            commands.append(command)
            await asyncio.sleep(0.18)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'TEMP_DIR', directory), \
                patch.object(main, 'TIMEOUT_SECONDS', 0.3), \
                patch.object(main, 'conversion_semaphore', asyncio.Semaphore(1)), \
                patch.object(main.shutil, 'which', return_value='heif-enc'), \
                patch.object(main, '_run_conversion_command', slow_command):
            with self.assertRaises(HTTPException) as raised:
                await main._perform_conversion(BackgroundTasks(), file, 'avif', 'lossless', 100)
            self.assertEqual(raised.exception.status_code, 504)
            self.assertEqual(len(commands), 2)
            self.assertIn('png:compression-level=1', commands[0])
            self.assertIn('speed=8', commands[1])
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertTrue(file.file.closed)

    async def test_success_retains_output_until_background_cleanup(self):
        file = upload()
        background = BackgroundTasks()

        async def fake_command(command):
            if command[0] == 'heif-enc':
                Path(command[command.index('--output') + 1]).write_bytes(b'encoded-output')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'TEMP_DIR', directory), \
                patch.object(main, 'conversion_semaphore', asyncio.Semaphore(1)), \
                patch.object(main.shutil, 'which', return_value='heif-enc'), \
                patch.object(main, '_run_conversion_command', fake_command):
            response = await main._perform_conversion(background, file, 'heif', 'lossy', 80)
            self.assertEqual(response.media_type, 'image/heif')
            self.assertEqual(Path(response.path).read_bytes(), b'encoded-output')
            self.assertTrue(file.file.closed)
            await background()
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_real_command_success_and_failure(self):
        await main._run_conversion_command([sys.executable, '-c', 'print("ok")'])
        with self.assertRaises(HTTPException) as raised:
            await main._run_conversion_command([sys.executable, '-c', 'import sys; sys.exit(2)'])
        self.assertEqual(raised.exception.status_code, 500)

    async def test_missing_executable_returns_503(self):
        with self.assertRaises(HTTPException) as raised:
            await main._run_conversion_command(['/nonexistent/image-encoder'])
        self.assertEqual(raised.exception.status_code, 503)

    async def test_cancel_during_launch_reaps_created_process(self):
        started = asyncio.Event()
        release = asyncio.Event()
        processes = []
        original = asyncio.subprocess.create_subprocess_exec

        async def delayed_launch(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            started.set()
            await release.wait()
            return process

        with patch.object(asyncio.subprocess, 'create_subprocess_exec', delayed_launch):
            task = asyncio.create_task(main._run_conversion_command([sys.executable, '-c', 'import time; time.sleep(30)']))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(processes[0].returncode)

    @unittest.skipUnless(os.name == 'posix', 'process groups require POSIX')
    async def test_cancel_kills_process_group_and_reaps_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'parent.pid'
            marker = Path(directory) / 'child-finished'
            child = 'import time; from pathlib import Path; time.sleep(0.4); Path(%r).write_text("orphan")' % str(marker)
            parent = ('import os, subprocess, sys, time; from pathlib import Path; '
                      'subprocess.Popen([sys.executable, "-c", %r]); '
                      'Path(%r).write_text(str(os.getpid())); time.sleep(30)') % (child, str(pid_file))
            task = asyncio.create_task(main._run_conversion_command([sys.executable, '-c', parent]))
            try:
                for _ in range(100):
                    if pid_file.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(pid_file.exists())
                pid = int(pid_file.read_text())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            await asyncio.sleep(0.5)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
