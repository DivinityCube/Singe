#!/usr/bin/env python3
"""
Comprehensive test suite for Singe CD burner application.
Tests all main classes and their core functionality.
"""

import unittest
import sys
import os
import time
import tempfile
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Add the current directory to path to import Singe
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import classes from Singe
from Singe import (
    ProgressBar,
    BurnJob,
    BatchBurnQueue,
    AudioCDWriter,
    MusicCDOrganizer,
    HelpSystem
)


class TestProgressBar(unittest.TestCase):
    """Test cases for the ProgressBar class."""
    
    def test_init(self):
        """Test ProgressBar initialization."""
        pb = ProgressBar(100, prefix='Test', suffix='suffix', length=50)
        self.assertEqual(pb.total, 100)
        self.assertEqual(pb.prefix, 'Test')
        self.assertEqual(pb.suffix, 'suffix')
        self.assertEqual(pb.length, 50)
        self.assertEqual(pb.current, 0)
    
    def test_update_increment(self):
        """Test ProgressBar update with auto-increment."""
        pb = ProgressBar(10)
        pb.update()
        self.assertEqual(pb.current, 1)
        pb.update()
        self.assertEqual(pb.current, 2)
    
    def test_update_set_current(self):
        """Test ProgressBar update with explicit current value."""
        pb = ProgressBar(100)
        pb.update(current=50)
        self.assertEqual(pb.current, 50)
    
    def test_update_suffix(self):
        """Test ProgressBar update with new suffix."""
        pb = ProgressBar(100, suffix='old')
        pb.update(suffix='new')
        self.assertEqual(pb.suffix, 'new')
    
    def test_format_time(self):
        """Test time formatting."""
        pb = ProgressBar(100)
        self.assertEqual(pb._format_time(0), "00:00")
        self.assertEqual(pb._format_time(59), "00:59")
        self.assertEqual(pb._format_time(60), "01:00")
        self.assertEqual(pb._format_time(125), "02:05")
        self.assertEqual(pb._format_time(3661), "61:01")
    
    def test_finish(self):
        """Test ProgressBar finish."""
        pb = ProgressBar(10)
        pb.finish()
        self.assertEqual(pb.current, pb.total)


class TestBurnJob(unittest.TestCase):
    """Test cases for the BurnJob class."""
    
    def test_init(self):
        """Test BurnJob initialization."""
        files = ['song1.mp3', 'song2.mp3']
        settings = {'normalize': True, 'speed': 8}
        job = BurnJob('Test Job', files, settings)
        
        self.assertEqual(job.name, 'Test Job')
        self.assertEqual(job.audio_files, files)
        self.assertEqual(job.settings, settings)
        self.assertEqual(job.status, 'pending')
        self.assertIsNone(job.error_message)
        self.assertIsNone(job.burn_time)
    
    def test_get_summary_pending(self):
        """Test get_summary for pending job."""
        job = BurnJob('Album 1', ['a.mp3', 'b.mp3'], {})
        summary = job.get_summary()
        self.assertIn('Album 1', summary)
        self.assertIn('2 tracks', summary)
        self.assertIn('⏸', summary)
    
    def test_get_summary_completed(self):
        """Test get_summary for completed job."""
        job = BurnJob('Album 1', ['a.mp3'], {})
        job.status = 'completed'
        summary = job.get_summary()
        self.assertIn('✓', summary)
    
    def test_get_summary_failed(self):
        """Test get_summary for failed job."""
        job = BurnJob('Album 1', ['a.mp3'], {})
        job.status = 'failed'
        summary = job.get_summary()
        self.assertIn('✗', summary)
    
    def test_get_summary_skipped(self):
        """Test get_summary for skipped job."""
        job = BurnJob('Album 1', ['a.mp3'], {})
        job.status = 'skipped'
        summary = job.get_summary()
        self.assertIn('⊘', summary)


class TestBatchBurnQueue(unittest.TestCase):
    """Test cases for the BatchBurnQueue class."""
    
    def test_init(self):
        """Test BatchBurnQueue initialization."""
        queue = BatchBurnQueue()
        self.assertEqual(len(queue.jobs), 0)
        self.assertEqual(queue.current_job_index, 0)
    
    def test_add_job(self):
        """Test adding jobs to queue."""
        queue = BatchBurnQueue()
        job1 = BurnJob('Job 1', ['a.mp3'], {})
        job2 = BurnJob('Job 2', ['b.mp3'], {})
        
        queue.add_job(job1)
        self.assertEqual(len(queue.jobs), 1)
        
        queue.add_job(job2)
        self.assertEqual(len(queue.jobs), 2)
    
    def test_remove_job(self):
        """Test removing jobs from queue."""
        queue = BatchBurnQueue()
        job1 = BurnJob('Job 1', ['a.mp3'], {})
        job2 = BurnJob('Job 2', ['b.mp3'], {})
        queue.add_job(job1)
        queue.add_job(job2)
        
        # Remove first job
        result = queue.remove_job(0)
        self.assertTrue(result)
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(queue.jobs[0].name, 'Job 2')
        
        # Try to remove invalid index
        result = queue.remove_job(10)
        self.assertFalse(result)
    
    def test_get_job(self):
        """Test getting job by index."""
        queue = BatchBurnQueue()
        job1 = BurnJob('Job 1', ['a.mp3'], {})
        queue.add_job(job1)
        
        # Valid index
        retrieved = queue.get_job(0)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, 'Job 1')
        
        # Invalid index
        retrieved = queue.get_job(10)
        self.assertIsNone(retrieved)
    
    def test_get_next_job(self):
        """Test getting next pending job."""
        queue = BatchBurnQueue()
        job1 = BurnJob('Job 1', ['a.mp3'], {})
        job2 = BurnJob('Job 2', ['b.mp3'], {})
        job3 = BurnJob('Job 3', ['c.mp3'], {})
        
        queue.add_job(job1)
        queue.add_job(job2)
        queue.add_job(job3)
        
        # Get first pending job
        next_job = queue.get_next_job()
        self.assertEqual(next_job.name, 'Job 1')
        
        # Mark first as completed, get next
        job1.status = 'completed'
        next_job = queue.get_next_job()
        self.assertEqual(next_job.name, 'Job 2')
        
        # Mark second as failed, get next
        job2.status = 'failed'
        next_job = queue.get_next_job()
        self.assertEqual(next_job.name, 'Job 3')
        
        # All done
        job3.status = 'completed'
        next_job = queue.get_next_job()
        self.assertIsNone(next_job)
    
    def test_get_summary_empty(self):
        """Test get_summary for empty queue."""
        queue = BatchBurnQueue()
        summary = queue.get_summary()
        self.assertIn('empty', summary.lower())
    
    def test_get_summary_with_jobs(self):
        """Test get_summary with various job statuses."""
        queue = BatchBurnQueue()
        job1 = BurnJob('Job 1', ['a.mp3'], {})
        job2 = BurnJob('Job 2', ['b.mp3'], {})
        job3 = BurnJob('Job 3', ['c.mp3'], {})
        job4 = BurnJob('Job 4', ['d.mp3'], {})
        
        job1.status = 'completed'
        job2.status = 'pending'
        job3.status = 'failed'
        job4.status = 'skipped'
        
        queue.add_job(job1)
        queue.add_job(job2)
        queue.add_job(job3)
        queue.add_job(job4)
        
        summary = queue.get_summary()
        self.assertIn('Total: 4', summary)
        self.assertIn('Pending: 1', summary)
        self.assertIn('Completed: 1', summary)
        self.assertIn('Failed: 1', summary)
        self.assertIn('Skipped: 1', summary)


class TestAudioCDWriter(unittest.TestCase):
    """Test cases for the AudioCDWriter class."""
    
    @patch('Singe.subprocess.run')
    def test_init(self, mock_run):
        """Test AudioCDWriter initialization."""
        # Mock device detection - need stderr to be iterable
        mock_run.return_value = Mock(
            stdout='',
            stderr='Device: /dev/sr0',
            returncode=0
        )
        
        writer = AudioCDWriter()
        self.assertIsNotNone(writer.device)
        self.assertEqual(writer.last_burn_wav_files, [])
        self.assertEqual(writer.last_burn_checksums, {})
    
    def test_audio_extensions(self):
        """Test that audio extensions are defined."""
        self.assertIn('.mp3', AudioCDWriter.AUDIO_EXTENSIONS)
        self.assertIn('.wav', AudioCDWriter.AUDIO_EXTENSIONS)
        self.assertIn('.flac', AudioCDWriter.AUDIO_EXTENSIONS)
    
    def test_cd_capacity_constants(self):
        """Test CD capacity constants."""
        self.assertEqual(AudioCDWriter.CD_74_MIN_SECONDS, 74 * 60)
        self.assertEqual(AudioCDWriter.CD_80_MIN_SECONDS, 80 * 60)
    
    def test_default_constants(self):
        """Test default gap and fade constants."""
        self.assertEqual(AudioCDWriter.DEFAULT_GAP_SECONDS, 2)
        self.assertEqual(AudioCDWriter.DEFAULT_FADE_IN, 0.0)
        self.assertEqual(AudioCDWriter.DEFAULT_FADE_OUT, 0.0)
    
    @patch('Singe.subprocess.run')
    def test_check_disc_status_no_disc(self, mock_run):
        """Test check_disc_status when no disc is inserted."""
        def mock_run_side_effect(*args, **kwargs):
            # First call: device detection
            if 'wodim' in args[0]:
                return Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
            # Second call: disc status check
            return Mock(stdout='', stderr='No disk', returncode=1)
        
        mock_run.side_effect = mock_run_side_effect
        
        writer = AudioCDWriter()
        status = writer.check_disc_status()
        
        self.assertFalse(status['inserted'])
    
    @patch('Singe.subprocess.run')
    def test_check_disc_status_blank_disc(self, mock_run):
        """Test check_disc_status with blank disc."""
        def mock_run_side_effect(*args, **kwargs):
            # First call: device detection
            if 'wodim' in args[0]:
                return Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
            # Second call: disc status check (cdrdao)
            return Mock(stdout='Disk is blank', stderr='', returncode=0)
        
        mock_run.side_effect = mock_run_side_effect
        
        writer = AudioCDWriter()
        status = writer.check_disc_status()
        
        self.assertTrue(status['inserted'])
        self.assertTrue(status['blank'])
        self.assertEqual(status['remaining_capacity'], AudioCDWriter.CD_80_MIN_SECONDS)
    
    @patch('Singe.subprocess.run')
    def test_calculate_file_checksum_md5(self, mock_run):
        """Test MD5 checksum calculation."""
        # Mock device detection
        mock_run.return_value = Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('test content')
            f.flush()
            temp_file = f.name
        
        try:
            writer = AudioCDWriter()
            checksum = writer.calculate_file_checksum(temp_file, 'md5')
            self.assertIsNotNone(checksum)
            self.assertEqual(len(checksum), 32)  # MD5 is 32 hex chars
        finally:
            os.unlink(temp_file)
    
    @patch('Singe.subprocess.run')
    def test_calculate_file_checksum_sha1(self, mock_run):
        """Test SHA1 checksum calculation."""
        # Mock device detection
        mock_run.return_value = Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('test content')
            f.flush()
            temp_file = f.name
        
        try:
            writer = AudioCDWriter()
            checksum = writer.calculate_file_checksum(temp_file, 'sha1')
            self.assertIsNotNone(checksum)
            self.assertEqual(len(checksum), 40)  # SHA1 is 40 hex chars
        finally:
            os.unlink(temp_file)
    
    @patch('Singe.subprocess.run')
    def test_calculate_file_checksum_sha256(self, mock_run):
        """Test SHA256 checksum calculation."""
        # Mock device detection
        mock_run.return_value = Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write('test content')
            f.flush()
            temp_file = f.name
        
        try:
            writer = AudioCDWriter()
            checksum = writer.calculate_file_checksum(temp_file, 'sha256')
            self.assertIsNotNone(checksum)
            self.assertEqual(len(checksum), 64)  # SHA256 is 64 hex chars
        finally:
            os.unlink(temp_file)
    
    @patch('Singe.subprocess.run')
    def test_calculate_file_checksum_nonexistent(self, mock_run):
        """Test checksum calculation for nonexistent file."""
        # Mock device detection
        mock_run.return_value = Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
        
        writer = AudioCDWriter()
        checksum = writer.calculate_file_checksum('/nonexistent/file.txt', 'sha256')
        self.assertIsNone(checksum)


class TestMusicCDOrganizer(unittest.TestCase):
    """Test cases for the MusicCDOrganizer class."""
    
    @patch('Singe.subprocess.run')
    def test_init(self, mock_run):
        """Test MusicCDOrganizer initialization."""
        # Mock device detection for AudioCDWriter
        mock_run.return_value = Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
        
        organizer = MusicCDOrganizer()
        self.assertIsNotNone(organizer.writer)
        self.assertIsInstance(organizer.writer, AudioCDWriter)
    
    @patch('Singe.subprocess.run')
    def test_organize_by_track_number(self, mock_run):
        """Test organizing files by track number."""
        # Mock device detection
        mock_run.return_value = Mock(stdout='', stderr='Device: /dev/sr0', returncode=0)
        
        organizer = MusicCDOrganizer()
        
        # Mock the extract_metadata method to return test data
        def mock_extract_metadata(file):
            metadata_map = {
                '/path/to/song1.mp3': {'track': '3', 'title': 'Song 3', 'artist': 'Artist A'},
                '/path/to/song2.mp3': {'track': '1', 'title': 'Song 1', 'artist': 'Artist A'},
                '/path/to/song3.mp3': {'track': '2', 'title': 'Song 2', 'artist': 'Artist A'},
            }
            return metadata_map.get(file, {'track': '999', 'title': 'Unknown', 'artist': 'Unknown'})
        
        organizer.writer.extract_metadata = mock_extract_metadata
        
        # Test organizing
        files = ['/path/to/song1.mp3', '/path/to/song2.mp3', '/path/to/song3.mp3']
        sorted_files = organizer.organize_by_track_number(files)
        
        # Should be sorted: song2 (track 1), song3 (track 2), song1 (track 3)
        self.assertEqual(len(sorted_files), 3)
        self.assertEqual(sorted_files[0], '/path/to/song2.mp3')
        self.assertEqual(sorted_files[1], '/path/to/song3.mp3')
        self.assertEqual(sorted_files[2], '/path/to/song1.mp3')


class TestHelpSystem(unittest.TestCase):
    """Test cases for the HelpSystem class."""
    
    def test_init(self):
        """Test HelpSystem initialization."""
        help_sys = HelpSystem()
        self.assertIsNotNone(help_sys)
    
    def test_multi_session_help(self):
        """Test multi-session help text."""
        help_sys = HelpSystem()
        help_text = help_sys.multi_session_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
        self.assertIn('multi-session', help_text.lower())
    
    def test_verification_help(self):
        """Test verification help text."""
        help_sys = HelpSystem()
        help_text = help_sys.verification_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_fade_effects_help(self):
        """Test fade effects help text."""
        help_sys = HelpSystem()
        help_text = help_sys.fade_effects_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_track_gaps_help(self):
        """Test track gaps help text."""
        help_sys = HelpSystem()
        help_text = help_sys.track_gaps_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_cdtext_help(self):
        """Test CD-Text help text."""
        help_sys = HelpSystem()
        help_text = help_sys.cdtext_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_folder_scanning_help(self):
        """Test folder scanning help text."""
        help_sys = HelpSystem()
        help_text = help_sys.folder_scanning_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_playlist_help(self):
        """Test playlist help text."""
        help_sys = HelpSystem()
        help_text = help_sys.playlist_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
        self.assertIn('M3U', help_text)
    
    def test_preview_help(self):
        """Test preview help text."""
        help_sys = HelpSystem()
        help_text = help_sys.preview_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_normalize_audio_help(self):
        """Test normalize audio help text."""
        help_sys = HelpSystem()
        help_text = help_sys.normalize_audio_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_track_order_help(self):
        """Test track order help text."""
        help_sys = HelpSystem()
        help_text = help_sys.track_order_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_burn_speed_help(self):
        """Test burn speed help text."""
        help_sys = HelpSystem()
        help_text = help_sys.burn_speed_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_cd_media_help(self):
        """Test CD media help text."""
        help_sys = HelpSystem()
        help_text = help_sys.cd_media_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_format_export_help(self):
        """Test format export help text."""
        help_sys = HelpSystem()
        help_text = help_sys.format_export_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_album_art_help(self):
        """Test album art help text."""
        help_sys = HelpSystem()
        help_text = help_sys.album_art_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)
    
    def test_batch_burn_help(self):
        """Test batch burn help text."""
        help_sys = HelpSystem()
        help_text = help_sys.batch_burn_help()
        self.assertIsInstance(help_text, str)
        self.assertGreater(len(help_text), 0)


def run_test_suite():
    """Run the complete test suite and report results."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestProgressBar))
    suite.addTests(loader.loadTestsFromTestCase(TestBurnJob))
    suite.addTests(loader.loadTestsFromTestCase(TestBatchBurnQueue))
    suite.addTests(loader.loadTestsFromTestCase(TestAudioCDWriter))
    suite.addTests(loader.loadTestsFromTestCase(TestMusicCDOrganizer))
    suite.addTests(loader.loadTestsFromTestCase(TestHelpSystem))
    
    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_test_suite()
    sys.exit(0 if success else 1)
