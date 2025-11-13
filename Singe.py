#!/usr/bin/env python3

import os
import sys
import subprocess
import tempfile
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
from datetime import timedelta
import hashlib
import time
import urllib.request
import urllib.parse
import urllib.error
import base64
import mimetypes
import shutil

class ProgressBar:
    """Simple progress bar for terminal display."""
    
    def __init__(self, total: int, prefix: str = '', suffix: str = '', length: int = 50):
        """
        Initialize progress bar.
        
        Args:
            total: Total number of iterations
            prefix: Prefix string
            suffix: Suffix string
            length: Character length of bar
        """
        self.total = total
        self.prefix = prefix
        self.suffix = suffix
        self.length = length
        self.current = 0
        self.start_time = time.time()
    
    def update(self, current: Optional[int] = None, suffix: Optional[str] = None):
        """
        Update progress bar.
        
        Args:
            current: Current iteration (increments by 1 if None)
            suffix: Optional new suffix text
        """
        if current is not None:
            self.current = current
        else:
            self.current += 1
        
        if suffix is not None:
            self.suffix = suffix
        
        # Calculate progress
        percent = 100 * (self.current / float(self.total))
        filled = int(self.length * self.current // self.total)
        bar = '█' * filled + '░' * (self.length - filled)
        
        # Calculate time
        elapsed = time.time() - self.start_time
        if self.current > 0:
            eta = elapsed * (self.total - self.current) / self.current
            eta_str = self._format_time(eta)
        else:
            eta_str = "--:--"
        
        # Print progress bar
        print(f'\r{self.prefix} |{bar}| {percent:.1f}% {self.suffix} ETA: {eta_str}', end='', flush=True)
        
        # Print newline on completion
        if self.current >= self.total:
            elapsed_str = self._format_time(elapsed)
            print(f'\r{self.prefix} |{bar}| 100.0% {self.suffix} Done in {elapsed_str}', flush=True)
    
    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"
    
    def finish(self):
        """Complete the progress bar."""
        self.update(self.total)

class BurnJob:
    """Represents a single CD burn job in a batch queue."""
    
    def __init__(self, name: str, audio_files: List[str], settings: Dict):
        """
        Initialize a burn job.
        
        Args:
            name: Name/description of this burn job
            audio_files: List of audio file paths for this CD
            settings: Dictionary of burn settings (normalize, speed, gaps, fades, etc.)
        """
        self.name = name
        self.audio_files = audio_files
        self.settings = settings
        self.status = 'pending'  # pending, completed, failed, skipped
        self.error_message = None
        self.burn_time = None
    
    def get_summary(self) -> str:
        """Get a summary string for this job."""
        file_count = len(self.audio_files)
        status_icon = {
            'pending': '⏸',
            'completed': '✓',
            'failed': '✗',
            'skipped': '⊘'
        }.get(self.status, '?')
        
        return f"{status_icon} {self.name} ({file_count} tracks)"

class BatchBurnQueue:
    """Manages a queue of CD burn jobs for sequential processing."""
    
    def __init__(self):
        self.jobs: List[BurnJob] = []
        self.current_job_index = 0
    
    def add_job(self, job: BurnJob):
        """Add a job to the queue."""
        self.jobs.append(job)
    
    def remove_job(self, index: int) -> bool:
        """Remove a job from the queue."""
        if 0 <= index < len(self.jobs):
            del self.jobs[index]
            return True
        return False
    
    def get_job(self, index: int) -> Optional[BurnJob]:
        """Get a job by index."""
        if 0 <= index < len(self.jobs):
            return self.jobs[index]
        return None
    
    def get_next_job(self) -> Optional[BurnJob]:
        """Get the next pending job."""
        for i in range(self.current_job_index, len(self.jobs)):
            if self.jobs[i].status == 'pending':
                self.current_job_index = i
                return self.jobs[i]
        return None
    
    def get_summary(self) -> str:
        """Get a summary of the queue."""
        if not self.jobs:
            return "Queue is empty"
        
        pending = sum(1 for j in self.jobs if j.status == 'pending')
        completed = sum(1 for j in self.jobs if j.status == 'completed')
        failed = sum(1 for j in self.jobs if j.status == 'failed')
        skipped = sum(1 for j in self.jobs if j.status == 'skipped')
        
        summary = f"Total: {len(self.jobs)} jobs | "
        summary += f"Pending: {pending} | Completed: {completed}"
        if failed > 0:
            summary += f" | Failed: {failed}"
        if skipped > 0:
            summary += f" | Skipped: {skipped}"
        
        return summary
    
    def display_queue(self):
        """Display the current queue status."""
        print("\n" + "="*70)
        print("BATCH BURN QUEUE")
        print("="*70)
        
        if not self.jobs:
            print("\nQueue is empty")
        else:
            print(f"\n{self.get_summary()}\n")
            print("-"*70)
            
            for i, job in enumerate(self.jobs, 1):
                print(f"{i}. {job.get_summary()}")
                if job.status == 'failed' and job.error_message:
                    print(f"   Error: {job.error_message}")
                elif job.status == 'completed' and job.burn_time:
                    print(f"   Burn time: {job.burn_time:.1f}s")
        
        print("="*70)

class AudioCDWriter:
    # Supported audio file extensions
    AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.opus', '.mp4', '.m4v'}
    
    # CD capacity constants
    CD_74_MIN_SECONDS = 74 * 60  # 4440 seconds
    CD_80_MIN_SECONDS = 80 * 60  # 4800 seconds
    
    # Default gap/pause between tracks (in seconds)
    DEFAULT_GAP_SECONDS = 2
    
    # Default fade durations (in seconds)
    DEFAULT_FADE_IN = 0.0
    DEFAULT_FADE_OUT = 0.0
    
    def __init__(self):
        self.device = self._detect_cd_device()
        self.last_burn_wav_files = []  # Store WAV files for verification
        self.last_burn_checksums = {}  # Store checksums for verification
    
    def check_disc_status(self) -> Dict:
        """
        Check if a disc is inserted and its current status.
        
        Returns:
            Dictionary with disc information
        """
        try:
            # Use cdrdao to check disc status
            result = subprocess.run(
                ['cdrdao', 'disk-info', '--device', self.device],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            disc_info = {
                'inserted': False,
                'blank': False,
                'appendable': False,
                'finalized': False,
                'sessions': 0,
                'tracks': 0,
                'used_capacity': 0,
                'remaining_capacity': 0
            }
            
            output = result.stderr + result.stdout
            
            # Parse output
            if 'No disk' in output or 'Cannot' in output or 'not ready' in output.lower():
                return disc_info
            
            disc_info['inserted'] = True
            
            # Check if blank
            if 'blank' in output.lower():
                disc_info['blank'] = True
                disc_info['remaining_capacity'] = self.CD_80_MIN_SECONDS
                return disc_info
            
            # Check if appendable (not finalized)
            if 'appendable' in output.lower() or 'open' in output.lower():
                disc_info['appendable'] = True
            elif 'complete' in output.lower() or 'closed' in output.lower():
                disc_info['finalized'] = True
            
            # Try to get track info
            try:
                para_result = subprocess.run(
                    ['cdparanoia', '-Q'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                # Count tracks
                track_count = 0
                for line in para_result.stderr.split('\n'):
                    if re.match(r'^\s*\d+\.', line):
                        track_count += 1
                
                disc_info['tracks'] = track_count
                
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            
            return disc_info
            
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"Error checking disc status: {e}")
            return {'inserted': False}
        except Exception as e:
            print(f"Unexpected error checking disc status: {e}")
            return {'inserted': False}

    def display_disc_status(self, disc_info: Dict):
        """Display formatted disc status information."""
        print("\n" + "="*70)
        print("DISC STATUS")
        print("="*70)
        
        if not disc_info['inserted']:
            print("✗ No disc inserted")
            print("  Please insert a disc and try again.")
        elif disc_info['blank']:
            print("✓ Blank disc detected")
            print(f"  Available capacity: {self._format_time(disc_info['remaining_capacity'])}")
        elif disc_info['appendable']:
            print("✓ Appendable disc detected (multi-session capable)")
            print(f"  Existing tracks: {disc_info['tracks']}")
            print("  Status: Open for additional sessions")
            print("\n  You can add more tracks to this disc!")
        elif disc_info['finalized']:
            print("⚠ Finalized disc detected")
            print(f"  Existing tracks: {disc_info['tracks']}")
            print("  Status: Closed/Finalized")
            print("\n  This disc cannot accept additional tracks.")
            print("  Use a CD-RW and erase it, or use a different disc.")
        else:
            print("? Unknown disc status")
            print(f"  Existing tracks: {disc_info['tracks']}")
        
        print("="*70)
        
    def calculate_file_checksum(self, file_path: str, algorithm: str = 'sha256') -> Optional[str]:
        """
        Calculate checksum of a file.
        
        Args:
            file_path: Path to the file
            algorithm: Hash algorithm ('md5', 'sha1', 'sha256')
            
        Returns:
            Hexadecimal checksum string or None if error
        """
        try:
            if algorithm == 'md5':
                hasher = hashlib.md5()
            elif algorithm == 'sha1':
                hasher = hashlib.sha1()
            else:  # sha256
                hasher = hashlib.sha256()
            
            with open(file_path, 'rb') as f:
                # Read in chunks to handle large files
                while chunk := f.read(8192):
                    hasher.update(chunk)
            
            return hasher.hexdigest()
        except Exception as e:
            print(f"Error calculating checksum for {file_path}: {e}")
            return None
    
    def verify_burned_disc(self, original_wav_files: List[str], 
                          original_checksums: Dict[str, str],
                          verify_method: str = 'full') -> bool:
        """
        Verify a burned audio CD by reading it back and comparing.
        
        Args:
            original_wav_files: List of original WAV file paths
            original_checksums: Dictionary mapping file paths to checksums
            verify_method: 'quick' (track count), 'standard' (duration), or 'full' (bit-perfect)
            
        Returns:
            True if verification passed, False otherwise
        """
        print("\n" + "="*70)
        print("CD VERIFICATION MODE")
        print("="*70)
        
        if verify_method == 'quick':
            return self._verify_quick(original_wav_files)
        elif verify_method == 'standard':
            return self._verify_standard(original_wav_files)
        else:  # full
            return self._verify_full(original_wav_files, original_checksums)
    
    def _verify_quick(self, original_wav_files: List[str]) -> bool:
        """Quick verification: Check if disc is readable and has correct track count."""
        print("\nPerforming QUICK verification...")
        print("Checking: Disc readability, track count")
        print("-" * 70)
        
        # Read CD track information
        tracks = self.read_audio_cd_tracks()
        
        if not tracks:
            print("✗ FAILED: Could not read disc or no tracks found")
            return False
        
        expected_tracks = len(original_wav_files)
        actual_tracks = len(tracks)
        
        print(f"\nExpected tracks: {expected_tracks}")
        print(f"Actual tracks:   {actual_tracks}")
        
        if expected_tracks == actual_tracks:
            print("\n✓ QUICK VERIFICATION PASSED")
            print("  - Disc is readable")
            print(f"  - Track count matches ({actual_tracks} tracks)")
            return True
        else:
            print(f"\n✗ VERIFICATION FAILED")
            print(f"  - Track count mismatch!")
            return False
    
    def _verify_standard(self, original_wav_files: List[str]) -> bool:
        """Standard verification: Check track count and durations."""
        print("\nPerforming STANDARD verification...")
        print("Checking: Disc readability, track count, track durations")
        print("-" * 70)
        
        # Read CD track information
        tracks = self.read_audio_cd_tracks()
        
        if not tracks:
            print("✗ FAILED: Could not read disc or no tracks found")
            return False
        
        expected_tracks = len(original_wav_files)
        actual_tracks = len(tracks)
        
        print(f"\nTrack count verification:")
        print(f"  Expected: {expected_tracks}")
        print(f"  Actual:   {actual_tracks}")
        
        if expected_tracks != actual_tracks:
            print("✗ Track count mismatch!")
            return False
        
        print("  ✓ Track count matches")
        
        # Verify durations
        print(f"\nDuration verification:")
        all_durations_ok = True
        
        for i, (track, orig_file) in enumerate(zip(tracks, original_wav_files), 1):
            # Get original file duration
            orig_duration = self.get_audio_duration(orig_file)
            
            if orig_duration is None:
                print(f"  Track {i}: [Could not determine original duration]")
                continue
            
            # Parse CD track duration (format: MM:SS.FF)
            cd_duration_str = track['length']
            try:
                # Parse MM:SS.FF format
                if ':' in cd_duration_str:
                    parts = cd_duration_str.split(':')
                    minutes = int(parts[0])
                    seconds_parts = parts[1].split('.')
                    seconds = int(seconds_parts[0])
                    cd_duration = minutes * 60 + seconds
                else:
                    cd_duration = float(cd_duration_str)
                
                # Allow 2 second tolerance for gaps/encoding
                duration_diff = abs(orig_duration - cd_duration)
                
                if duration_diff <= 2:
                    print(f"  Track {i}: ✓ {self._format_time(orig_duration)} vs {self._format_time(cd_duration)}")
                else:
                    print(f"  Track {i}: ✗ Duration mismatch! {self._format_time(orig_duration)} vs {self._format_time(cd_duration)}")
                    all_durations_ok = False
            except Exception as e:
                print(f"  Track {i}: [Could not parse CD duration: {e}]")
        
        if all_durations_ok:
            print("\n✓ STANDARD VERIFICATION PASSED")
            print("  - Disc is readable")
            print("  - Track count matches")
            print("  - All track durations match (within tolerance)")
            return True
        else:
            print("\n✗ VERIFICATION FAILED")
            print("  - Duration mismatches detected")
            return False
    
    def _verify_full(self, original_wav_files: List[str], 
                    original_checksums: Dict[str, str]) -> bool:
        """Full bit-perfect verification: Rip tracks and compare checksums."""
        print("\nPerforming FULL BIT-PERFECT verification...")
        print("Checking: Disc readability, track count, bit-perfect audio data")
        print("This will take several minutes as tracks are ripped from the CD...")
        print("-" * 70)
        
        # Check if cdparanoia is available
        try:
            subprocess.run(['which', 'cdparanoia'], capture_output=True, check=True)
        except subprocess.CalledProcessError:
            print("✗ cdparanoia not found. Install with: sudo apt-get install cdparanoia")
            print("  Full verification requires cdparanoia to rip tracks.")
            return False
        
        # Create temporary directory for ripped tracks
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"\nRipping tracks from CD for verification...")
            
            # Read CD track information
            tracks = self.read_audio_cd_tracks()
            
            if not tracks:
                print("✗ FAILED: Could not read disc or no tracks found")
                return False
            
            expected_tracks = len(original_wav_files)
            actual_tracks = len(tracks)
            
            print(f"\nTrack count: {actual_tracks} (expected: {expected_tracks})")
            
            if expected_tracks != actual_tracks:
                print("✗ Track count mismatch!")
                return False
            
            # Rip each track and compare
            verification_results = []
            
            for i, (track, orig_file) in enumerate(zip(tracks, original_wav_files), 1):
                track_num = track['number']
                ripped_file = os.path.join(temp_dir, f"verify_track_{track_num:02d}.wav")
                
                print(f"\n  Track {i}/{actual_tracks}: Ripping...")
                
                # Rip the track
                result = subprocess.run(
                    ['cdparanoia', '-w', str(track_num), ripped_file],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    print(f"    ✗ Failed to rip track {i}")
                    verification_results.append({
                        'track': i,
                        'status': 'failed',
                        'message': 'Rip failed'
                    })
                    continue
                
                print(f"    Calculating checksum...")
                
                # Calculate checksum of ripped track
                ripped_checksum = self.calculate_file_checksum(ripped_file, 'sha256')
                
                if ripped_checksum is None:
                    print(f"    ✗ Failed to calculate checksum")
                    verification_results.append({
                        'track': i,
                        'status': 'failed',
                        'message': 'Checksum calculation failed'
                    })
                    continue
                
                # Get original checksum
                orig_checksum = original_checksums.get(orig_file)
                
                if orig_checksum is None:
                    print(f"    ⚠ No original checksum available for comparison")
                    verification_results.append({
                        'track': i,
                        'status': 'unknown',
                        'message': 'No original checksum'
                    })
                    continue
                
                # Compare checksums
                if ripped_checksum == orig_checksum:
                    print(f"    ✓ Bit-perfect match!")
                    print(f"      Checksum: {ripped_checksum[:16]}...")
                    verification_results.append({
                        'track': i,
                        'status': 'passed',
                        'checksum': ripped_checksum
                    })
                else:
                    print(f"    ✗ Checksum mismatch!")
                    print(f"      Original:  {orig_checksum[:16]}...")
                    print(f"      Ripped:    {ripped_checksum[:16]}...")
                    verification_results.append({
                        'track': i,
                        'status': 'failed',
                        'message': 'Checksum mismatch',
                        'orig_checksum': orig_checksum,
                        'ripped_checksum': ripped_checksum
                    })
            
            # Print verification summary
            print("\n" + "="*70)
            print("VERIFICATION SUMMARY")
            print("="*70)
            
            passed = sum(1 for r in verification_results if r['status'] == 'passed')
            failed = sum(1 for r in verification_results if r['status'] == 'failed')
            unknown = sum(1 for r in verification_results if r['status'] == 'unknown')
            
            print(f"\nTotal tracks:  {len(verification_results)}")
            print(f"Passed:        {passed} ✓")
            print(f"Failed:        {failed} {'✗' if failed > 0 else ''}")
            print(f"Unknown:       {unknown} {'⚠' if unknown > 0 else ''}")
            
            if failed > 0:
                print("\n✗ VERIFICATION FAILED")
                print("\nFailed tracks:")
                for result in verification_results:
                    if result['status'] == 'failed':
                        print(f"  Track {result['track']}: {result['message']}")
                return False
            elif passed == len(verification_results):
                print("\n✓✓✓ FULL BIT-PERFECT VERIFICATION PASSED ✓✓✓")
                print("\nAll tracks are bit-perfect copies of the original audio!")
                print("Your CD burn was 100% successful.")
                return True
            else:
                print("\n⚠ VERIFICATION INCOMPLETE")
                print(f"{unknown} track(s) could not be fully verified.")
                print("The burn may be successful, but verification is inconclusive.")
                return False
    
    def choose_verification_method(self) -> Optional[str]:
        """
        Interactive menu to choose verification method.
        
        Returns:
            Verification method string or None to skip
        """
        print("\n" + "="*70)
        print("CD VERIFICATION OPTIONS")
        print("="*70)
        print("\nVerify your burned CD to ensure data integrity.")
        
        while True:
            print("\nVerification methods:")
            print("1. Quick verification (30 seconds)")
            print("   - Checks disc readability and track count")
            print("   - Fast but minimal verification")
            print()
            print("2. Standard verification (1-2 minutes)")
            print("   - Checks track count and durations")
            print("   - Good balance of speed and thoroughness")
            print()
            print("3. Full bit-perfect verification (5-10 minutes)")
            print("   - Rips tracks back and compares checksums")
            print("   - Guarantees 100% accurate burn")
            print("   - Recommended for archival/important burns")
            print()
            print("4. Skip verification")
            print("5. View verification help")
            
            choice = input("\nSelect option (1-5): ").strip()
            
            if choice == '1':
                return 'quick'
            elif choice == '2':
                return 'standard'
            elif choice == '3':
                return 'full'
            elif choice == '4':
                return None
            elif choice == '5':
                print(HelpSystem.verification_help())
            else:
                print("Invalid option. Please try again.")
    
    def extract_metadata(self, audio_file: str) -> Dict[str, str]:
        """
        Extract metadata from audio file using ffprobe.
        
        Args:
            audio_file: Path to the audio file
            
        Returns:
            Dictionary with metadata fields
        """
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_format', audio_file],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                tags = data.get('format', {}).get('tags', {})
                
                # Handle case-insensitive tag names
                metadata = {}
                for key, value in tags.items():
                    metadata[key.lower()] = value
                
                return {
                    'title': metadata.get('title', Path(audio_file).stem),
                    'artist': metadata.get('artist', 'Unknown Artist'),
                    'album': metadata.get('album', 'Unknown Album'),
                    'track': metadata.get('track', '0'),
                    'genre': metadata.get('genre', ''),
                    'date': metadata.get('date', ''),
                    'composer': metadata.get('composer', ''),
                    'performer': metadata.get('performer', metadata.get('artist', 'Unknown Artist')),
                    'duration': data.get('format', {}).get('duration', '0')
                }
        except Exception as e:
            print(f"Warning: Could not extract metadata from {audio_file}: {e}")
        
        return {
            'title': Path(audio_file).stem,
            'artist': 'Unknown Artist',
            'album': 'Unknown Album',
            'track': '0',
            'genre': '',
            'date': '',
            'composer': '',
            'performer': 'Unknown Artist',
            'duration': '0'
        }
    
    def calculate_disc_id(self, wav_files: List[str]) -> Optional[str]:
        """
        Calculate CDDB disc ID for a list of WAV files.
        
        Args:
            wav_files: List of WAV file paths
            
        Returns:
            CDDB disc ID string or None if calculation fails
        """
        try:
            track_offsets = [150]  # First track starts at 150 frames (2 seconds)
            total_frames = 150
            
            for wav_file in wav_files:
                duration = self.get_audio_duration(wav_file)
                if duration is None:
                    return None
                
                frames = int(duration * 75)  # 75 frames per second
                total_frames += frames
                track_offsets.append(total_frames)
            
            # Calculate total disc length in seconds
            disc_length = total_frames // 75
            
            # Calculate checksum
            n = 0
            for offset in track_offsets[:-1]:  # Exclude the lead-out offset
                seconds = offset // 75
                n += sum(int(d) for d in str(seconds))
            
            # CDDB disc ID formula
            num_tracks = len(wav_files)
            disc_id = ((n % 0xff) << 24 | disc_length << 8 | num_tracks)
            
            return f"{disc_id:08x}"
            
        except Exception as e:
            print(f"Error calculating disc ID: {e}")
            return None
    
    def query_musicbrainz(self, disc_id: str, num_tracks: int, track_durations: List[int]) -> Optional[Dict]:
        """
        Query MusicBrainz database for CD metadata.
        
        Args:
            disc_id: CDDB disc ID
            num_tracks: Number of tracks
            track_durations: List of track durations in frames
            
        Returns:
            Dictionary with album and track metadata or None if not found
        """
        try:
            # build MusicBrainz discid calculation
            # note: MusicBrainz uses a different algorithm than CDDB
            # For now, i'll use the CDDB ID to search
            
            print(f"\nQuerying MusicBrainz database (Disc ID: {disc_id})...")
            
            # MusicBrainz API endpoint
            base_url = "https://musicbrainz.org/ws/2/discid/"
            
            # construct the query URL
            # note: this is a simplified approach. full implementation would need proper MusicBrainz disc ID calculation
            url = f"{base_url}{disc_id}?fmt=json&inc=recordings+artist-credits"
            
            # set user agent (required by MusicBrainz API)
            headers = {
                'User-Agent': 'AudioCDWriter/1.0 (https://github.com/DivinityCube/Singe)'
            }
            
            req = urllib.request.Request(url, headers=headers)
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if 'releases' in data and len(data['releases']) > 0:
                    release = data['releases'][0]
                    
                    album_info = {
                        'title': release.get('title', 'Unknown Album'),
                        'artist': release.get('artist-credit-phrase', 'Unknown Artist'),
                        'date': release.get('date', ''),
                        'country': release.get('country', ''),
                        'barcode': release.get('barcode', '')
                    }
                    
                    tracks_info = []
                    if 'media' in release and len(release['media']) > 0:
                        media = release['media'][0]
                        if 'tracks' in media:
                            for track in media['tracks']:
                                track_info = {
                                    'title': track.get('title', 'Unknown'),
                                    'artist': track.get('artist-credit-phrase', album_info['artist']),
                                    'length': track.get('length', 0)
                                }
                                tracks_info.append(track_info)
                    
                    print("✓ Match found in MusicBrainz database!")
                    return {
                        'album': album_info,
                        'tracks': tracks_info,
                        'source': 'MusicBrainz'
                    }
                else:
                    print("✗ No matches found in MusicBrainz database")
                    return None
                    
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print("✗ Disc not found in MusicBrainz database")
            else:
                print(f"HTTP Error {e.code}: {e.reason}")
            return None
        except Exception as e:
            print(f"Error querying MusicBrainz: {e}")
            return None
    
    def query_cddb(self, disc_id: str, num_tracks: int, track_offsets: List[int], 
                   disc_length: int) -> Optional[Dict]:
        """
        Query freedb/CDDB database for CD metadata.
        
        Args:
            disc_id: CDDB disc ID
            num_tracks: Number of tracks
            track_offsets: List of track frame offsets
            disc_length: Total disc length in seconds
            
        Returns:
            Dictionary with album and track metadata or None if not found
        """
        try:
            print(f"\nQuerying CDDB database (Disc ID: {disc_id})...")
            
            # use gnudb.org as a freedb mirror (freedb.org is discontinued)
            server = "gnudb.gnudb.org"
            
            # Build query string
            offsets_str = ' '.join(str(o) for o in track_offsets)
            query_data = {
                'cmd': f'cddb query {disc_id} {num_tracks} {offsets_str} {disc_length}',
                'hello': 'user localhost AudioCDWriter 1.0',
                'proto': '6'
            }
            
            query_string = urllib.parse.urlencode(query_data)
            url = f"http://{server}/~cddb/cddb.cgi?{query_string}"
            
            with urllib.request.urlopen(url, timeout=10) as response:
                result = response.read().decode('utf-8')
                lines = result.strip().split('\n')
                
                if not lines:
                    print("✗ Empty response from CDDB")
                    return None
                
                status_line = lines[0]
                status_code = status_line.split()[0]
                
                if status_code == '200':
                    # Exact match found
                    parts = status_line.split(maxsplit=3)
                    if len(parts) >= 4:
                        category = parts[1]
                        disc_id_response = parts[2]
                        title = parts[3]
                        
                        # Read full entry
                        return self._read_cddb_entry(server, category, disc_id_response)
                        
                elif status_code.startswith('21'):
                    # Multiple matches - use first one
                    if len(lines) > 1:
                        match_line = lines[1]
                        parts = match_line.split(maxsplit=2)
                        if len(parts) >= 3:
                            category = parts[0]
                            disc_id_response = parts[1]
                            
                            print(f"Multiple matches found, using first match...")
                            return self._read_cddb_entry(server, category, disc_id_response)
                
                elif status_code == '202':
                    print("✗ No match found in CDDB database")
                    return None
                else:
                    print(f"✗ CDDB query failed: {status_line}")
                    return None
                    
        except Exception as e:
            print(f"Error querying CDDB: {e}")
            return None
    
    def _read_cddb_entry(self, server: str, category: str, disc_id: str) -> Optional[Dict]:
        """
        Read a full CDDB entry.
        
        Args:
            server: CDDB server address
            category: Music category
            disc_id: Disc ID
            
        Returns:
            Dictionary with parsed CDDB data
        """
        try:
            query_data = {
                'cmd': f'cddb read {category} {disc_id}',
                'hello': 'user localhost AudioCDWriter 1.0',
                'proto': '6'
            }
            
            query_string = urllib.parse.urlencode(query_data)
            url = f"http://{server}/~cddb/cddb.cgi?{query_string}"
            
            with urllib.request.urlopen(url, timeout=10) as response:
                result = response.read().decode('utf-8', errors='ignore')
                lines = result.strip().split('\n')
                
                album_data = {
                    'title': 'Unknown Album',
                    'artist': 'Unknown Artist',
                    'genre': category,
                    'date': ''
                }
                
                tracks_data = []
                
                for line in lines:
                    if line.startswith('DTITLE='):
                        dtitle = line.split('=', 1)[1]
                        if ' / ' in dtitle:
                            artist, title = dtitle.split(' / ', 1)
                            album_data['artist'] = artist.strip()
                            album_data['title'] = title.strip()
                        else:
                            album_data['title'] = dtitle.strip()
                    
                    elif line.startswith('DYEAR='):
                        album_data['date'] = line.split('=', 1)[1].strip()
                    
                    elif line.startswith('TTITLE'):
                        match = re.match(r'TTITLE(\d+)=(.*)', line)
                        if match:
                            track_num = int(match.group(1))
                            track_title = match.group(2).strip()
                            
                            # Ensure tracks list is large enough
                            while len(tracks_data) <= track_num:
                                tracks_data.append({
                                    'title': f'Track {len(tracks_data) + 1}',
                                    'artist': album_data['artist']
                                })
                            
                            # Parse artist / title format
                            if ' / ' in track_title:
                                artist, title = track_title.split(' / ', 1)
                                tracks_data[track_num]['artist'] = artist.strip()
                                tracks_data[track_num]['title'] = title.strip()
                            else:
                                tracks_data[track_num]['title'] = track_title
                
                print("✓ Match found in CDDB database!")
                return {
                    'album': album_data,
                    'tracks': tracks_data,
                    'source': 'CDDB'
                }
                
        except Exception as e:
            print(f"Error reading CDDB entry: {e}")
            return None
    
    def lookup_cd_metadata(self, wav_files: List[str]) -> Optional[Dict]:
        """
        Lookup CD metadata from online databases (CDDB/MusicBrainz).
        
        Args:
            wav_files: List of WAV files that will be burned
            
        Returns:
            Dictionary with album and track metadata or None if not found
        """
        print("\n" + "="*70)
        print("CD METADATA LOOKUP")
        print("="*70)
        print("\nAttempting to identify CD from audio fingerprint...")
        
        # Calculate disc ID
        disc_id = self.calculate_disc_id(wav_files)
        if not disc_id:
            print("✗ Could not calculate disc ID")
            return None
        
        # Get track information for queries
        track_offsets = [150]  # First track at 2 seconds
        track_durations = []
        total_frames = 150
        
        for wav_file in wav_files:
            duration = self.get_audio_duration(wav_file)
            if duration:
                frames = int(duration * 75)
                track_durations.append(frames)
                total_frames += frames
                track_offsets.append(total_frames)
        
        disc_length = total_frames // 75
        num_tracks = len(wav_files)
        
        # we'll try MusicBrainz first (more modern and actively maintained)
        metadata = self.query_musicbrainz(disc_id, num_tracks, track_durations)
        
        # then fall back to CDDB if MusicBrainz doesn't find anything
        if not metadata:
            print("\nFalling back to CDDB database...")
            metadata = self.query_cddb(disc_id, num_tracks, track_offsets[:-1], disc_length)
        
        if metadata:
            self._display_lookup_results(metadata)
        
        return metadata
    
    def _display_lookup_results(self, metadata: Dict):
        """Display the results of a metadata lookup."""
        print("\n" + "="*70)
        print(f"METADATA FOUND ({metadata.get('source', 'Unknown')})")
        print("="*70)
        
        album = metadata.get('album', {})
        tracks = metadata.get('tracks', [])
        
        print(f"\nAlbum: {album.get('title', 'Unknown')}")
        print(f"Artist: {album.get('artist', 'Unknown')}")
        if album.get('date'):
            print(f"Year: {album.get('date')}")
        if album.get('genre'):
            print(f"Genre: {album.get('genre')}")
        
        print(f"\nTracks ({len(tracks)}):")
        print("-" * 70)
        for i, track in enumerate(tracks, 1):
            title = track.get('title', f'Track {i}')
            artist = track.get('artist', album.get('artist', 'Unknown'))
            
            if artist != album.get('artist'):
                print(f"  {i:2d}. {title} - {artist}")
            else:
                print(f"  {i:2d}. {title}")
        
        print("="*70)
    
    def apply_lookup_metadata(self, metadata: Dict, wav_files: List[str]) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
        """
        Apply looked-up metadata to create track and album metadata structures.
        
        Args:
            metadata: Metadata from CDDB/MusicBrainz lookup
            wav_files: List of WAV files
            
        Returns:
            Tuple of (tracks_metadata, album_info)
        """
        album = metadata.get('album', {})
        tracks = metadata.get('tracks', [])
        
        album_info = {
            'title': album.get('title', 'Audio CD'),
            'artist': album.get('artist', 'Various Artists'),
            'genre': album.get('genre', ''),
            'date': album.get('date', '')
        }
        
        tracks_metadata = []
        for i, wav_file in enumerate(wav_files):
            if i < len(tracks):
                track = tracks[i]
                track_metadata = {
                    'title': track.get('title', f'Track {i+1}'),
                    'artist': track.get('artist', album_info['artist']),
                    'performer': track.get('artist', album_info['artist']),
                    'album': album_info['title'],
                    'track': str(i + 1),
                    'genre': album_info['genre'],
                    'date': album_info['date'],
                    'composer': track.get('composer', ''),
                    'duration': str(self.get_audio_duration(wav_file) or 0)
                }
            else:
                # another fallback if not enough track info
                track_metadata = {
                    'title': f'Track {i+1}',
                    'artist': album_info['artist'],
                    'performer': album_info['artist'],
                    'album': album_info['title'],
                    'track': str(i + 1),
                    'genre': album_info['genre'],
                    'date': album_info['date'],
                    'composer': '',
                    'duration': str(self.get_audio_duration(wav_file) or 0)
                }
            
            tracks_metadata.append(track_metadata)
        
        return tracks_metadata, album_info
    
    def embed_album_art(self, audio_file: str, image_file: str) -> bool:
        """
        Embed album art into an audio file using ffmpeg.
        
        Args:
            audio_file: Path to the audio file
            image_file: Path to the image file (JPG, PNG, etc.)
            
        Returns:
            True if successful, False otherwise
        """
        if not os.path.exists(audio_file):
            print(f"✗ Audio file not found: {audio_file}")
            return False
        
        if not os.path.exists(image_file):
            print(f"✗ Image file not found: {image_file}")
            return False
        
        # Check if image file is valid
        image_ext = Path(image_file).suffix.lower()
        if image_ext not in ['.jpg', '.jpeg', '.png', '.bmp', '.gif']:
            print(f"✗ Unsupported image format: {image_ext}")
            print("  Supported: JPG, PNG, BMP, GIF")
            return False
        
        audio_ext = Path(audio_file).suffix.lower()
        temp_output = audio_file + '.tmp' + audio_ext
        
        try:
            # Format-specific embedding
            if audio_ext == '.mp3':
                # MP3 uses ID3v2 tags
                result = subprocess.run([
                    'ffmpeg', '-i', audio_file, '-i', image_file,
                    '-map', '0:a', '-map', '1:0',
                    '-c', 'copy',
                    '-id3v2_version', '3',
                    '-metadata:s:v', 'title=Album cover',
                    '-metadata:s:v', 'comment=Cover (front)',
                    temp_output, '-y'
                ], capture_output=True, text=True)
            
            elif audio_ext in ['.m4a', '.mp4', '.m4v']:
                # M4A/MP4 uses MP4 metadata
                result = subprocess.run([
                    'ffmpeg', '-i', audio_file, '-i', image_file,
                    '-map', '0:a', '-map', '1:0',
                    '-c', 'copy',
                    '-disposition:v:0', 'attached_pic',
                    temp_output, '-y'
                ], capture_output=True, text=True)
            
            elif audio_ext == '.flac':
                # FLAC supports embedded pictures
                result = subprocess.run([
                    'ffmpeg', '-i', audio_file, '-i', image_file,
                    '-map', '0:a', '-map', '1:0',
                    '-c', 'copy',
                    '-metadata:s:v', 'title=Album cover',
                    '-metadata:s:v', 'comment=Cover (front)',
                    '-disposition:v:0', 'attached_pic',
                    temp_output, '-y'
                ], capture_output=True, text=True)
            
            elif audio_ext == '.ogg':
                # OGG Vorbis supports embedded pictures
                result = subprocess.run([
                    'ffmpeg', '-i', audio_file, '-i', image_file,
                    '-map', '0:a', '-map', '1:0',
                    '-c', 'copy',
                    '-metadata:s:v', 'title=Album cover',
                    '-metadata:s:v', 'comment=Cover (front)',
                    temp_output, '-y'
                ], capture_output=True, text=True)
            
            else:
                print(f"✗ Album art not supported for {audio_ext} format")
                print("  Supported: MP3, M4A, FLAC, OGG")
                return False
            
            if result.returncode == 0:
                # Replace original file with the new one
                os.replace(temp_output, audio_file)
                return True
            else:
                print(f"✗ Error embedding album art: {result.stderr}")
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                return False
        
        except FileNotFoundError:
            print("✗ ffmpeg not installed. Install with: sudo apt-get install ffmpeg")
            return False
        except Exception as e:
            print(f"✗ Error: {e}")
            if os.path.exists(temp_output):
                os.remove(temp_output)
            return False
    
    def extract_album_art(self, audio_file: str, output_file: Optional[str] = None) -> Optional[str]:
        """
        Extract album art from an audio file.
        
        Args:
            audio_file: Path to the audio file
            output_file: Optional output path for the image (auto-generated if None)
            
        Returns:
            Path to extracted image file or None if no art found/error
        """
        if not os.path.exists(audio_file):
            print(f"✗ Audio file not found: {audio_file}")
            return None
        
        try:
            # First, check if the file has embedded art
            probe_result = subprocess.run([
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                audio_file
            ], capture_output=True, text=True)
            
            if probe_result.returncode != 0:
                return None
            
            data = json.loads(probe_result.stdout)
            has_video = False
            
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    has_video = True
                    break
            
            if not has_video:
                return None
            
            # Generate output filename if not provided
            if output_file is None:
                base_name = Path(audio_file).stem
                output_file = f"{base_name}_cover.jpg"
            
            # Extract the album art
            result = subprocess.run([
                'ffmpeg', '-i', audio_file,
                '-an', '-vcodec', 'copy',
                output_file, '-y'
            ], capture_output=True, text=True)
            
            if result.returncode == 0 and os.path.exists(output_file):
                return output_file
            else:
                return None
        
        except Exception as e:
            print(f"✗ Error extracting album art: {e}")
            return None
    
    def check_album_art(self, audio_file: str) -> Dict:
        """
        Check if an audio file has embedded album art.
        
        Args:
            audio_file: Path to the audio file
            
        Returns:
            Dictionary with album art information
        """
        info = {
            'has_art': False,
            'format': None,
            'width': None,
            'height': None,
            'size': None
        }
        
        if not os.path.exists(audio_file):
            return info
        
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                audio_file
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                
                for stream in data.get('streams', []):
                    if stream.get('codec_type') == 'video':
                        info['has_art'] = True
                        info['format'] = stream.get('codec_name', 'unknown')
                        info['width'] = stream.get('width')
                        info['height'] = stream.get('height')
                        break
        
        except Exception:
            pass
        
        return info
    
    def batch_embed_album_art(self, audio_files: List[str], image_file: str) -> Dict[str, bool]:
        """
        Embed the same album art into multiple audio files.
        
        Args:
            audio_files: List of audio file paths
            image_file: Path to the image file
            
        Returns:
            Dictionary mapping file paths to success status
        """
        print("\n" + "="*70)
        print("BATCH ALBUM ART EMBEDDING")
        print("="*70)
        print(f"\nEmbedding art into {len(audio_files)} file(s)")
        print(f"Image: {Path(image_file).name}")
        print("-"*70)
        
        results = {}
        success_count = 0
        
        progress = ProgressBar(len(audio_files), prefix='Embedding art:', suffix='', length=40)
        
        for i, audio_file in enumerate(audio_files, 1):
            track_name = Path(audio_file).name[:30]
            progress.update(i, suffix=track_name)
            
            success = self.embed_album_art(audio_file, image_file)
            results[audio_file] = success
            
            if success:
                success_count += 1
        
        print("\n" + "="*70)
        print("BATCH EMBEDDING SUMMARY")
        print("="*70)
        print(f"\nSuccessfully embedded: {success_count}/{len(audio_files)}")
        print(f"Failed: {len(audio_files) - success_count}/{len(audio_files)}")
        print("="*70)
        
        return results
    
    def album_art_manager_interactive(self):
        """
        Interactive menu for managing album art in audio files.
        """
        while True:
            print("\n" + "="*70)
            print("ALBUM ART MANAGER")
            print("="*70)
            print("\nOptions:")
            print("1. Embed album art into audio file(s)")
            print("2. Extract album art from audio file")
            print("3. Check if file(s) have album art")
            print("4. Remove album art from file(s)")
            print("5. Batch embed art into folder")
            print("6. Back to main menu")
            
            choice = input("\nSelect option (1-6): ").strip()
            
            if choice == '1':
                # Embed album art
                print("\n" + "-"*70)
                print("EMBED ALBUM ART")
                print("-"*70)
                
                image_path = input("\nEnter path to image file (JPG, PNG, etc.): ").strip()
                
                if not os.path.exists(image_path):
                    print(f"✗ Image file not found: {image_path}")
                    continue
                
                print("\nEnter audio file paths (one per line, empty line to finish):")
                audio_files = []
                while True:
                    file_path = input().strip()
                    if not file_path:
                        break
                    audio_files.append(file_path)
                
                if not audio_files:
                    print("✗ No audio files specified")
                    continue
                
                if len(audio_files) == 1:
                    success = self.embed_album_art(audio_files[0], image_path)
                    if success:
                        print("\n✓ Album art embedded successfully!")
                    else:
                        print("\n✗ Failed to embed album art")
                else:
                    self.batch_embed_album_art(audio_files, image_path)
            
            elif choice == '2':
                # Extract album art
                print("\n" + "-"*70)
                print("EXTRACT ALBUM ART")
                print("-"*70)
                
                audio_path = input("\nEnter path to audio file: ").strip()
                
                if not os.path.exists(audio_path):
                    print(f"✗ Audio file not found: {audio_path}")
                    continue
                
                output_path = input("Enter output image path (or press Enter for auto): ").strip()
                if not output_path:
                    output_path = None
                
                print("\nExtracting album art...")
                extracted = self.extract_album_art(audio_path, output_path)
                
                if extracted:
                    print(f"✓ Album art extracted to: {extracted}")
                    
                    # Show image info
                    if os.path.exists(extracted):
                        size_kb = os.path.getsize(extracted) / 1024
                        print(f"  File size: {size_kb:.1f} KB")
                else:
                    print("✗ No album art found in this file")
            
            elif choice == '3':
                # Check for album art
                print("\n" + "-"*70)
                print("CHECK ALBUM ART")
                print("-"*70)
                
                print("\nEnter audio file paths (one per line, empty line to finish):")
                audio_files = []
                while True:
                    file_path = input().strip()
                    if not file_path:
                        break
                    audio_files.append(file_path)
                
                if not audio_files:
                    print("✗ No audio files specified")
                    continue
                
                print("\n" + "="*70)
                print("ALBUM ART STATUS")
                print("="*70)
                
                for audio_file in audio_files:
                    print(f"\n{Path(audio_file).name}")
                    
                    if not os.path.exists(audio_file):
                        print("  ✗ File not found")
                        continue
                    
                    info = self.check_album_art(audio_file)
                    
                    if info['has_art']:
                        print("  ✓ Has album art")
                        if info['width'] and info['height']:
                            print(f"    Dimensions: {info['width']}x{info['height']}")
                        if info['format']:
                            print(f"    Format: {info['format'].upper()}")
                    else:
                        print("  ✗ No album art")
            
            elif choice == '4':
                # Remove album art
                print("\n" + "-"*70)
                print("REMOVE ALBUM ART")
                print("-"*70)
                
                print("\nEnter audio file paths (one per line, empty line to finish):")
                audio_files = []
                while True:
                    file_path = input().strip()
                    if not file_path:
                        break
                    audio_files.append(file_path)
                
                if not audio_files:
                    print("✗ No audio files specified")
                    continue
                
                confirm = input(f"\nRemove album art from {len(audio_files)} file(s)? (y/n): ").strip().lower()
                if confirm != 'y':
                    print("Cancelled")
                    continue
                
                success_count = 0
                for i, audio_file in enumerate(audio_files, 1):
                    print(f"\n[{i}/{len(audio_files)}] {Path(audio_file).name}")
                    
                    if not os.path.exists(audio_file):
                        print("  ✗ File not found")
                        continue
                    
                    audio_ext = Path(audio_file).suffix.lower()
                    temp_output = audio_file + '.tmp' + audio_ext
                    
                    try:
                        # Strip all video streams (album art)
                        result = subprocess.run([
                            'ffmpeg', '-i', audio_file,
                            '-map', '0:a', '-c', 'copy',
                            temp_output, '-y'
                        ], capture_output=True, text=True)
                        
                        if result.returncode == 0:
                            os.replace(temp_output, audio_file)
                            print("  ✓ Album art removed")
                            success_count += 1
                        else:
                            print("  ✗ Failed to remove album art")
                            if os.path.exists(temp_output):
                                os.remove(temp_output)
                    except Exception as e:
                        print(f"  ✗ Error: {e}")
                        if os.path.exists(temp_output):
                            os.remove(temp_output)
                
                print(f"\n✓ Removed album art from {success_count}/{len(audio_files)} file(s)")
            
            elif choice == '5':
                # Batch embed from folder
                print("\n" + "-"*70)
                print("BATCH EMBED FROM FOLDER")
                print("-"*70)
                
                folder_path = input("\nEnter folder path: ").strip()
                
                if not os.path.isdir(folder_path):
                    print(f"✗ Folder not found: {folder_path}")
                    continue
                
                image_path = input("Enter path to image file: ").strip()
                
                if not os.path.exists(image_path):
                    print(f"✗ Image file not found: {image_path}")
                    continue
                
                recursive = input("Scan subdirectories? (y/n): ").strip().lower() == 'y'
                
                audio_files = self.scan_folder_for_audio(folder_path, recursive)
                
                if not audio_files:
                    print("✗ No audio files found in folder")
                    continue
                
                confirm = input(f"\nEmbed album art into {len(audio_files)} file(s)? (y/n): ").strip().lower()
                if confirm == 'y':
                    self.batch_embed_album_art(audio_files, image_path)
            
            elif choice == '6':
                break
            
            else:
                print("Invalid option")
    
    def batch_burn_interactive(self, queue: BatchBurnQueue):
        """
        Interactive batch burn execution - processes all jobs in queue.
        
        Args:
            queue: BatchBurnQueue with jobs to process
        """
        if not queue.jobs:
            print("\n✗ Queue is empty. Add jobs first.")
            return
        
        # Display queue summary
        queue.display_queue()
        
        pending_count = sum(1 for j in queue.jobs if j.status == 'pending')
        
        if pending_count == 0:
            print("\n✗ No pending jobs in queue.")
            return
        
        print(f"\nReady to burn {pending_count} CD(s) sequentially.")
        print("\nIMPORTANT:")
        print("- Have blank CDs ready")
        print("- You'll be prompted to insert each disc")
        print("- Process cannot be paused once started")
        print("- Each job uses its configured settings")
        
        confirm = input("\nStart batch burn process? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Batch burn cancelled.")
            return
        
        # Process each job
        print("\n" + "="*70)
        print("BATCH BURN EXECUTION")
        print("="*70)
        
        start_time = time.time()
        jobs_completed = 0
        jobs_failed = 0
        
        # Initialize batch progress bar
        batch_progress = ProgressBar(pending_count, prefix='Batch progress:', suffix='Complete', length=50)
        batch_progress.update(0)
        
        while True:
            job = queue.get_next_job()
            if not job:
                break
            
            job_num = queue.current_job_index + 1
            
            print("\n" + "="*70)
            print(f"JOB {job_num}/{len(queue.jobs)}: {job.name}")
            print("="*70)
            print(f"Tracks: {len(job.audio_files)}")
            print(f"Settings: Speed={job.settings.get('speed', 8)}x, "
                  f"Normalize={job.settings.get('normalize', True)}, "
                  f"CD-TEXT={job.settings.get('use_cdtext', True)}")
            
            # Wait for user to insert disc
            print("\n" + "-"*70)
            input(f"Insert blank CD for '{job.name}' and press Enter...")
            print("-"*70)
            
            # Check disc status
            print("\nChecking disc status...")
            disc_info = self.check_disc_status()
            
            if not disc_info['inserted']:
                print("\n✗ No disc detected!")
                choice = input("Skip this job? (y/n): ").strip().lower()
                if choice == 'y':
                    job.status = 'skipped'
                    job.error_message = "No disc inserted"
                    continue
                else:
                    print("Aborting batch burn.")
                    break
            
            if not disc_info['blank']:
                print("\n⚠ WARNING: Disc is not blank!")
                choice = input("Continue anyway? (y/n): ").strip().lower()
                if choice != 'y':
                    job.status = 'skipped'
                    job.error_message = "Disc not blank"
                    continue
            
            # Execute burn
            print(f"\nBurning '{job.name}'...")
            job_start = time.time()
            
            try:
                success = self.burn_audio_cd(
                    audio_files=job.audio_files,
                    normalize=job.settings.get('normalize', True),
                    speed=job.settings.get('speed', 8),
                    dry_run=False,
                    use_cdtext=job.settings.get('use_cdtext', True),
                    track_gaps=job.settings.get('track_gaps'),
                    fade_ins=job.settings.get('fade_ins'),
                    fade_outs=job.settings.get('fade_outs'),
                    multi_session=job.settings.get('multi_session', False),
                    finalize=job.settings.get('finalize', True)
                )
                
                job.burn_time = time.time() - job_start
                
                if success:
                    job.status = 'completed'
                    jobs_completed += 1
                    batch_progress.update(jobs_completed)
                    print(f"\n✓ '{job.name}' burned successfully in {job.burn_time:.1f}s")
                else:
                    job.status = 'failed'
                    job.error_message = "Burn operation failed"
                    jobs_failed += 1
                    batch_progress.update(jobs_completed)
                    print(f"\n✗ '{job.name}' failed")
                    
                    choice = input("\nContinue with remaining jobs? (y/n): ").strip().lower()
                    if choice != 'y':
                        print("Aborting batch burn.")
                        break
            
            except Exception as e:
                job.status = 'failed'
                job.error_message = str(e)
                jobs_failed += 1
                batch_progress.update(jobs_completed)
                print(f"\n✗ Error burning '{job.name}': {e}")
                
                choice = input("\nContinue with remaining jobs? (y/n): ").strip().lower()
                if choice != 'y':
                    print("Aborting batch burn.")
                    break
        
        # Finalize batch progress bar
        batch_progress.finish()
        
        # Final summary
        total_time = time.time() - start_time
        
        print("\n" + "="*70)
        print("BATCH BURN SUMMARY")
        print("="*70)
        print(f"\nTotal jobs: {len(queue.jobs)}")
        print(f"Completed: {jobs_completed}")
        print(f"Failed: {jobs_failed}")
        print(f"Skipped: {sum(1 for j in queue.jobs if j.status == 'skipped')}")
        print(f"Pending: {sum(1 for j in queue.jobs if j.status == 'pending')}")
        print(f"\nTotal time: {total_time/60:.1f} minutes")
        
        if jobs_completed > 0:
            avg_time = sum(j.burn_time for j in queue.jobs if j.burn_time) / jobs_completed
            print(f"Average burn time: {avg_time:.1f}s per CD")
        
        print("\n" + "-"*70)
        print("Detailed results:")
        for i, job in enumerate(queue.jobs, 1):
            print(f"  {i}. {job.get_summary()}")
            if job.error_message:
                print(f"     Error: {job.error_message}")
        
        print("="*70)
    
    def configure_fades(self, num_tracks: int, track_names: List[str]) -> Tuple[List[float], List[float]]:
        """
        Interactive configuration for fade in/out effects.
        
        Args:
            num_tracks: Number of tracks on the disc
            track_names: List of track names for reference
            
        Returns:
            Tuple of (fade_in_list, fade_out_list) with durations in seconds
        """
        print("\n" + "="*70)
        print("FADE IN/OUT CONFIGURATION")
        print("="*70)
        print("\nFades create smooth transitions at the beginning (fade in) or")
        print("end (fade out) of tracks, gradually increasing or decreasing volume.")
        
        while True:
            print("\nOptions:")
            print("1. No fades (keep audio as-is)")
            print("2. Standard fade out (3 seconds at end of all tracks)")
            print("3. Standard fade in/out (2s in, 3s out on all tracks)")
            print("4. Custom fade for all tracks")
            print("5. Individual fade control per track")
            print("6. Preset: DJ mix (short crossfade-ready fades)")
            print("7. Preset: Radio-style (fade out only)")
            print("8. Preset: Gentle transitions (longer fades)")
            print("9. View fade effects help")
            
            choice = input("\nSelect option (1-9): ").strip()
            
            if choice == '1':
                # No fades
                fade_ins = [0.0] * num_tracks
                fade_outs = [0.0] * num_tracks
                print("✓ No fades will be applied")
                return fade_ins, fade_outs
            
            elif choice == '2':
                # Standard fade out only
                fade_ins = [0.0] * num_tracks
                fade_outs = [3.0] * num_tracks
                print("✓ 3-second fade out on all tracks")
                return fade_ins, fade_outs
            
            elif choice == '3':
                # Standard fade in/out
                fade_ins = [2.0] * num_tracks
                fade_outs = [3.0] * num_tracks
                print("✓ 2-second fade in, 3-second fade out on all tracks")
                return fade_ins, fade_outs
            
            elif choice == '4':
                # Custom uniform fades
                try:
                    fade_in = float(input("Enter fade in duration in seconds (0-10): ").strip())
                    fade_out = float(input("Enter fade out duration in seconds (0-10): ").strip())
                    
                    if 0 <= fade_in <= 10 and 0 <= fade_out <= 10:
                        fade_ins = [fade_in] * num_tracks
                        fade_outs = [fade_out] * num_tracks
                        print(f"✓ {fade_in}s fade in, {fade_out}s fade out on all tracks")
                        return fade_ins, fade_outs
                    else:
                        print("Fade durations must be between 0 and 10 seconds")
                except ValueError:
                    print("Invalid input. Please enter numbers.")
            
            elif choice == '5':
                # Individual fade configuration
                fade_ins = []
                fade_outs = []
                
                print(f"\nConfiguring fades for {num_tracks} tracks")
                print("(Press Enter to use defaults: 0s fade in, 0s fade out)\n")
                
                for i in range(num_tracks):
                    track_name = track_names[i] if i < len(track_names) else f"Track {i+1}"
                    # Truncate long names
                    if len(track_name) > 50:
                        track_name = track_name[:47] + "..."
                    
                    print(f"\n--- {track_name} ---")
                    
                    while True:
                        fade_in_input = input(f"  Fade in duration (0-10s, default 0): ").strip()
                        if fade_in_input == '':
                            fade_ins.append(0.0)
                            break
                        try:
                            fade_in = float(fade_in_input)
                            if 0 <= fade_in <= 10:
                                fade_ins.append(fade_in)
                                break
                            else:
                                print("  Fade in must be between 0 and 10 seconds")
                        except ValueError:
                            print("  Invalid input. Please enter a number.")
                    
                    while True:
                        fade_out_input = input(f"  Fade out duration (0-10s, default 0): ").strip()
                        if fade_out_input == '':
                            fade_outs.append(0.0)
                            break
                        try:
                            fade_out = float(fade_out_input)
                            if 0 <= fade_out <= 10:
                                fade_outs.append(fade_out)
                                break
                            else:
                                print("  Fade out must be between 0 and 10 seconds")
                        except ValueError:
                            print("  Invalid input. Please enter a number.")
                
                # Display summary
                print("\n--- Fade Configuration Summary ---")
                for i, (track_name, fade_in, fade_out) in enumerate(zip(track_names, fade_ins, fade_outs), 1):
                    if len(track_name) > 40:
                        track_name = track_name[:37] + "..."
                    if fade_in > 0 or fade_out > 0:
                        print(f"  Track {i}: {track_name}")
                        if fade_in > 0:
                            print(f"    Fade in: {fade_in}s")
                        if fade_out > 0:
                            print(f"    Fade out: {fade_out}s")
                
                confirm = input("\nConfirm this configuration? (y/n): ").strip().lower()
                if confirm == 'y':
                    return fade_ins, fade_outs
                else:
                    print("Restarting fade configuration...")
            
            elif choice == '6':
                # DJ mix preset (short fades for seamless mixing)
                fade_ins = [0.5] * num_tracks
                fade_outs = [0.5] * num_tracks
                print("✓ DJ mix preset: 0.5s fade in/out (crossfade-ready)")
                return fade_ins, fade_outs
            
            elif choice == '7':
                # Radio-style preset (fade out only)
                fade_ins = [0.0] * num_tracks
                fade_outs = [4.0] * num_tracks
                print("✓ Radio-style preset: 4s fade out (no fade in)")
                return fade_ins, fade_outs
            
            elif choice == '8':
                # Gentle transitions preset
                fade_ins = [3.0] * num_tracks
                fade_outs = [5.0] * num_tracks
                print("✓ Gentle transitions: 3s fade in, 5s fade out")
                return fade_ins, fade_outs
            
            elif choice == '9':
                print(HelpSystem.fade_effects_help())
            
            else:
                print("Invalid option. Please try again.")
    
    def display_fade_preview(self, track_names: List[str], fade_ins: List[float], fade_outs: List[float]):
        """
        Display a preview of tracks with their fade settings.
        
        Args:
            track_names: List of track names
            fade_ins: List of fade in durations
            fade_outs: List of fade out durations
        """
        print("\n" + "="*70)
        print("FADE EFFECTS PREVIEW")
        print("="*70)
        
        has_fades = False
        for i, (name, fade_in, fade_out) in enumerate(zip(track_names, fade_ins, fade_outs), 1):
            # Truncate long names for display
            display_name = name if len(name) <= 45 else name[:42] + "..."
            
            if fade_in > 0 or fade_out > 0:
                has_fades = True
                fade_desc = []
                if fade_in > 0:
                    fade_desc.append(f"↗{fade_in}s in")
                if fade_out > 0:
                    fade_desc.append(f"↘{fade_out}s out")
                
                print(f"  Track {i:2d}: {display_name}")
                print(f"           {', '.join(fade_desc)}")
            else:
                print(f"  Track {i:2d}: {display_name} [no fades]")
        
        if not has_fades:
            print("\n  No fade effects will be applied to any tracks")
        
        print("="*70)
    
    def apply_fade_effects(self, input_file: str, output_file: str, fade_in: float, fade_out: float) -> bool:
        """
        Apply fade in/out effects to an audio file using ffmpeg.
        
        Args:
            input_file: Path to input audio file
            output_file: Path to output audio file
            fade_in: Fade in duration in seconds
            fade_out: Fade out duration in seconds
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get duration of the file
            duration = self.get_audio_duration(input_file)
            if duration is None:
                print(f"Warning: Could not determine duration of {input_file}")
                return False
            
            # Build ffmpeg filter
            filters = []
            
            if fade_in > 0:
                # Fade in from start
                filters.append(f"afade=t=in:st=0:d={fade_in}")
            
            if fade_out > 0:
                # Fade out before end
                fade_start = max(0, duration - fade_out)
                filters.append(f"afade=t=out:st={fade_start}:d={fade_out}")
            
            if not filters:
                # No fades, just copy
                result = subprocess.run(
                    ['ffmpeg', '-i', input_file, '-acodec', 'pcm_s16le',
                     '-ar', '44100', '-ac', '2', output_file, '-y'],
                    capture_output=True
                )
            else:
                # Apply fade filters
                filter_chain = ','.join(filters)
                result = subprocess.run(
                    ['ffmpeg', '-i', input_file, '-af', filter_chain,
                     '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
                     output_file, '-y'],
                    capture_output=True
                )
            
            return result.returncode == 0
            
        except Exception as e:
            print(f"Error applying fades: {e}")
            return False
    
    def configure_track_gaps(self, num_tracks: int) -> List[float]:
        """
        Interactive configuration for track gaps/pauses.
        
        Args:
            num_tracks: Number of tracks on the disc
            
        Returns:
            List of gap durations in seconds for each track
        """
        print("\n" + "="*70)
        print("TRACK GAP CONFIGURATION")
        print("="*70)
        print("\nTrack gaps are the silent pauses between songs on an audio CD.")
        print(f"Standard default: {self.DEFAULT_GAP_SECONDS} seconds between all tracks")
        
        while True:
            print("\nOptions:")
            print(f"1. Use standard gaps ({self.DEFAULT_GAP_SECONDS} seconds between all tracks)")
            print("2. Set custom gap for all tracks")
            print("3. No gaps (gapless playback)")
            print("4. Set individual gaps for each track")
            print("5. Preset: Live album (minimal 0.5s gaps)")
            print("6. Preset: DJ mix (no gaps)")
            print("7. Preset: Classical (3-4 second gaps)")
            print("8. View gap configuration help")
            
            choice = input("\nSelect option (1-8): ").strip()
            
            if choice == '1':
                # Standard 2-second gaps
                gaps = [self.DEFAULT_GAP_SECONDS] * num_tracks
                print(f"✓ Using standard {self.DEFAULT_GAP_SECONDS}-second gaps between all tracks")
                return gaps
            
            elif choice == '2':
                # Custom uniform gap
                try:
                    gap = float(input("Enter gap duration in seconds (0-10): ").strip())
                    if 0 <= gap <= 10:
                        gaps = [gap] * num_tracks
                        print(f"✓ Using {gap}-second gaps between all tracks")
                        return gaps
                    else:
                        print("Gap must be between 0 and 10 seconds")
                except ValueError:
                    print("Invalid input. Please enter a number.")
            
            elif choice == '3':
                # Gapless playback
                gaps = [0.0] * num_tracks
                print("✓ Gapless playback enabled (0-second gaps)")
                return gaps
            
            elif choice == '4':
                # Individual gap configuration
                gaps = []
                print(f"\nConfiguring gaps for {num_tracks} tracks")
                print("(The gap comes BEFORE each track)")
                
                for i in range(num_tracks):
                    while True:
                        if i == 0:
                            print(f"\nTrack 1: First track (gap before disc starts)")
                            gap_input = input(f"  Gap before track 1 (default 2, press Enter): ").strip()
                        else:
                            print(f"\nTrack {i+1}: Gap between track {i} and track {i+1}")
                            gap_input = input(f"  Gap duration in seconds (default {self.DEFAULT_GAP_SECONDS}, press Enter): ").strip()
                        
                        if gap_input == '':
                            gaps.append(self.DEFAULT_GAP_SECONDS)
                            break
                        
                        try:
                            gap = float(gap_input)
                            if 0 <= gap <= 10:
                                gaps.append(gap)
                                break
                            else:
                                print("  Gap must be between 0 and 10 seconds")
                        except ValueError:
                            print("  Invalid input. Please enter a number.")
                
                # Display summary
                print("\n--- Gap Configuration Summary ---")
                for i, gap in enumerate(gaps, 1):
                    print(f"  Track {i}: {gap}s gap before")
                
                confirm = input("\nConfirm this configuration? (y/n): ").strip().lower()
                if confirm == 'y':
                    return gaps
                else:
                    print("Restarting gap configuration...")
            
            elif choice == '5':
                # Live album preset
                gaps = [0.5] * num_tracks
                gaps[0] = 2.0  # Normal gap before first track
                print("✓ Live album preset: 0.5s gaps (2s before first track)")
                return gaps
            
            elif choice == '6':
                # DJ mix preset
                gaps = [0.0] * num_tracks
                print("✓ DJ mix preset: No gaps (seamless transitions)")
                return gaps
            
            elif choice == '7':
                # Classical preset
                gaps = [3.0] * num_tracks
                print("✓ Classical preset: 3-second gaps between movements")
                return gaps
            
            elif choice == '8':
                print(HelpSystem.track_gaps_help())
            
            else:
                print("Invalid option. Please try again.")
    
    def display_gap_preview(self, track_names: List[str], gaps: List[float]):
        """
        Display a preview of track order with gap information.
        
        Args:
            track_names: List of track names/filenames
            gaps: List of gap durations
        """
        print("\n" + "="*70)
        print("TRACK ORDER WITH GAPS PREVIEW")
        print("="*70)
        
        for i, (name, gap) in enumerate(zip(track_names, gaps), 1):
            # Truncate long names for display
            display_name = name if len(name) <= 50 else name[:47] + "..."
            
            if i == 1:
                print(f"  [{gap}s pause]")
                print(f"  Track {i:2d}: {display_name}")
            else:
                print(f"  [{gap}s pause]")
                print(f"  Track {i:2d}: {display_name}")
        
        print("="*70)
        
        # Calculate total gap time
        total_gap_time = sum(gaps)
        print(f"\nTotal gap time: {total_gap_time:.1f} seconds ({total_gap_time/60:.2f} minutes)")
    
    def display_cdtext_preview(self, tracks_metadata: List[Dict[str, str]], album_info: Dict[str, str]):
        """
        Display a preview of CD-TEXT information that will be embedded.
        
        Args:
            tracks_metadata: List of metadata dictionaries for each track
            album_info: Album-level metadata
        """
        print("\n" + "="*70)
        print("CD-TEXT PREVIEW")
        print("="*70)
        
        print(f"\nALBUM INFORMATION:")
        print(f"  Title:       {album_info.get('title', 'Various Artists')}")
        print(f"  Artist:      {album_info.get('artist', 'Various Artists')}")
        print(f"  Genre:       {album_info.get('genre', 'Unknown')}")
        print(f"  Year:        {album_info.get('date', 'Unknown')}")
        
        print(f"\nTRACK INFORMATION:")
        print("-" * 70)
        
        for i, metadata in enumerate(tracks_metadata, 1):
            title = metadata.get('title', f'Track {i}')
            artist = metadata.get('performer', metadata.get('artist', 'Unknown'))
            
            # Truncate if too long for display
            if len(title) > 40:
                title = title[:37] + "..."
            if len(artist) > 30:
                artist = artist[:27] + "..."
            
            print(f"  Track {i:2d}: {title}")
            print(f"            Artist: {artist}")
            
            if metadata.get('composer') and metadata['composer'] != metadata.get('artist', ''):
                composer = metadata['composer']
                if len(composer) > 30:
                    composer = composer[:27] + "..."
                print(f"            Composer: {composer}")
            
            if i < len(tracks_metadata):
                print()
        
        print("="*70)
        print("\nThis information will be readable by CD-TEXT compatible players")
        print("(most modern car stereos, home CD players, and computer drives)")
        print("="*70)
    
    def edit_cdtext_metadata(self, tracks_metadata: List[Dict[str, str]], album_info: Dict[str, str]) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
        """
        Interactive editor for CD-TEXT metadata.
        
        Args:
            tracks_metadata: List of track metadata dictionaries
            album_info: Album metadata dictionary
            
        Returns:
            Tuple of (updated tracks_metadata, updated album_info)
        """
        print("\n" + "="*70)
        print("CD-TEXT METADATA EDITOR")
        print("="*70)
        
        while True:
            print("\nOptions:")
            print("1. Edit album information")
            print("2. Edit track information")
            print("3. Edit specific track")
            print("4. Reset all metadata from files")
            print("5. Done editing")
            
            choice = input("\nSelect option (1-5): ").strip()
            
            if choice == '1':
                print("\n--- Album Information ---")
                print(f"Current Title: {album_info.get('title', '')}")
                new_title = input("New album title (press Enter to keep): ").strip()
                if new_title:
                    album_info['title'] = new_title
                
                print(f"Current Artist: {album_info.get('artist', '')}")
                new_artist = input("New album artist (press Enter to keep): ").strip()
                if new_artist:
                    album_info['artist'] = new_artist
                
                print(f"Current Genre: {album_info.get('genre', '')}")
                new_genre = input("New genre (press Enter to keep): ").strip()
                if new_genre:
                    album_info['genre'] = new_genre
                
                print(f"Current Year: {album_info.get('date', '')}")
                new_date = input("New year (press Enter to keep): ").strip()
                if new_date:
                    album_info['date'] = new_date
                
                print("✓ Album information updated")
            
            elif choice == '2':
                print("\n--- Bulk Track Edit ---")
                print("1. Set all tracks to same artist")
                print("2. Set all tracks to same album")
                print("3. Cancel")
                
                bulk_choice = input("Select option: ").strip()
                
                if bulk_choice == '1':
                    artist = input("Enter artist name for all tracks: ").strip()
                    if artist:
                        for metadata in tracks_metadata:
                            metadata['artist'] = artist
                            metadata['performer'] = artist
                        print(f"✓ Set all tracks to artist: {artist}")
                
                elif bulk_choice == '2':
                    album = input("Enter album name for all tracks: ").strip()
                    if album:
                        for metadata in tracks_metadata:
                            metadata['album'] = album
                        album_info['title'] = album
                        print(f"✓ Set all tracks to album: {album}")
            
            elif choice == '3':
                try:
                    track_num = int(input(f"\nEnter track number (1-{len(tracks_metadata)}): ").strip())
                    if 1 <= track_num <= len(tracks_metadata):
                        metadata = tracks_metadata[track_num - 1]
                        
                        print(f"\n--- Track {track_num} ---")
                        print(f"Current Title: {metadata.get('title', '')}")
                        new_title = input("New title (press Enter to keep): ").strip()
                        if new_title:
                            metadata['title'] = new_title
                        
                        print(f"Current Artist: {metadata.get('artist', '')}")
                        new_artist = input("New artist (press Enter to keep): ").strip()
                        if new_artist:
                            metadata['artist'] = new_artist
                            metadata['performer'] = new_artist
                        
                        print(f"Current Composer: {metadata.get('composer', '')}")
                        new_composer = input("New composer (press Enter to keep): ").strip()
                        if new_composer:
                            metadata['composer'] = new_composer
                        
                        print(f"✓ Track {track_num} updated")
                    else:
                        print("Invalid track number")
                except ValueError:
                    print("Invalid input")
            
            elif choice == '4':
                confirm = input("Reset all metadata? This will reload from files (y/n): ").strip().lower()
                if confirm == 'y':
                    print("Metadata reset - please reload from original function")
                    return tracks_metadata, album_info
            
            elif choice == '5':
                print("✓ Editing complete")
                break
            
            else:
                print("Invalid option")
        
        return tracks_metadata, album_info
    
    def sanitize_cdtext(self, text: str, max_length: int = 80) -> str:
        """
        Sanitize text for CD-TEXT compatibility.
        
        Args:
            text: Text to sanitize
            max_length: Maximum length (CD-TEXT has limits)
            
        Returns:
            Sanitized text
        """
        if not text:
            return ""
        
        # Remove or replace problematic characters
        # CD-TEXT supports ISO 8859-1, but ASCII is safest
        text = text.encode('ascii', 'replace').decode('ascii')
        
        # Replace special characters that might cause issues
        text = text.replace('"', "'")
        text = text.replace('\n', ' ')
        text = text.replace('\r', ' ')
        text = text.replace('\t', ' ')
        
        # Truncate if too long
        if len(text) > max_length:
            text = text[:max_length-3] + "..."
        
        return text
    
    def frames_from_seconds(self, seconds: float) -> int:
        """
        Convert seconds to CD frames (1/75th of a second).
        
        Args:
            seconds: Time in seconds
            
        Returns:
            Number of frames
        """
        return int(seconds * 75)
    
    def frames_to_msf(self, frames: int) -> str:
        """
        Convert frames to MSF (Minutes:Seconds:Frames) format.
        
        Args:
            frames: Number of frames
            
        Returns:
            MSF formatted string (MM:SS:FF)
        """
        minutes = frames // (75 * 60)
        frames %= (75 * 60)
        seconds = frames // 75
        frames %= 75
        
        return f"{minutes:02d}:{seconds:02d}:{frames:02d}"
    
    def generate_toc_with_cdtext(self, wav_files: List[str], tracks_metadata: List[Dict[str, str]], 
                                  album_info: Dict[str, str], output_file: str, gaps: List[float]):
        """
        Generate a TOC file with CD-TEXT information and custom track gaps.
        
        Args:
            wav_files: List of WAV file paths
            tracks_metadata: List of track metadata dictionaries
            album_info: Album-level metadata
            output_file: Path to output TOC file
            gaps: List of gap durations in seconds for each track
        """
        with open(output_file, 'w') as f:
            # CD format
            f.write("CD_DA\n\n")
            
            # Album-level CD-TEXT
            f.write("CD_TEXT {\n")
            f.write("  LANGUAGE_MAP {\n")
            f.write("    0 : EN\n")
            f.write("  }\n\n")
            
            f.write("  LANGUAGE 0 {\n")
            
            # Sanitize and write album info
            album_title = self.sanitize_cdtext(album_info.get('title', 'Audio CD'))
            album_artist = self.sanitize_cdtext(album_info.get('artist', 'Various Artists'))
            genre = self.sanitize_cdtext(album_info.get('genre', ''))
            
            f.write(f'    TITLE "{album_title}"\n')
            f.write(f'    PERFORMER "{album_artist}"\n')
            
            if genre:
                f.write(f'    GENRE "{genre}"\n')
            
            f.write("  }\n")
            f.write("}\n\n")
            
            # Track information with CD-TEXT and custom gaps
            for i, (wav_file, metadata, gap) in enumerate(zip(wav_files, tracks_metadata, gaps), 1):
                f.write(f"// Track {i}\n")
                f.write("TRACK AUDIO\n")
                
                # Track-level CD-TEXT
                f.write("CD_TEXT {\n")
                f.write("  LANGUAGE 0 {\n")
                
                # Sanitize track metadata
                title = self.sanitize_cdtext(metadata.get('title', f'Track {i}'))
                performer = self.sanitize_cdtext(metadata.get('performer', metadata.get('artist', 'Unknown')))
                composer = self.sanitize_cdtext(metadata.get('composer', ''))
                
                f.write(f'    TITLE "{title}"\n')
                f.write(f'    PERFORMER "{performer}"\n')
                
                if composer and composer != performer:
                    f.write(f'    COMPOSER "{composer}"\n')
                
                f.write("  }\n")
                f.write("}\n\n")
                
                # Pregap (silence before track)
                if gap > 0:
                    gap_msf = self.frames_to_msf(self.frames_from_seconds(gap))
                    f.write(f"PREGAP {gap_msf}\n")
                
                # Audio file
                f.write(f'FILE "{wav_file}" 0\n\n')
    
    def preview_tracks(self, audio_files: List[str], preview_seconds: int = 10):
        """
        Preview audio tracks by playing a few seconds of each.
        
        Args:
            audio_files: List of audio file paths
            preview_seconds: Number of seconds to play from each track
        """
        print("\n" + "="*70)
        print("TRACK PREVIEW MODE")
        print("="*70)
        print(f"Playing {preview_seconds} seconds from each track...")
        print("Press Ctrl+C to skip to next track or 'q' to quit preview\n")
        
        # Check if ffplay is available
        try:
            subprocess.run(['which', 'ffplay'], capture_output=True, check=True)
        except subprocess.CalledProcessError:
            print("✗ ffplay not installed. Install with: sudo apt-get install ffmpeg")
            return
        
        for i, audio_file in enumerate(audio_files, 1):
            if not os.path.exists(audio_file):
                print(f"Track {i}: {Path(audio_file).name} - [FILE NOT FOUND]")
                continue
            
            print(f"\n▶ Track {i}/{len(audio_files)}: {Path(audio_file).name}")
            print(f"   Playing {preview_seconds} seconds...")
            
            try:
                # Use ffplay to preview the track
                process = subprocess.Popen(
                    ['ffplay', '-autoexit', '-nodisp', '-t', str(preview_seconds),
                     '-loglevel', 'quiet', audio_file],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                # Wait for playback to finish or user to interrupt
                process.wait()
                
            except KeyboardInterrupt:
                # User pressed Ctrl+C to skip
                if process.poll() is None:
                    process.terminate()
                    process.wait()
                print("   [Skipped]")
                
                # Ask if user wants to continue previewing
                try:
                    response = input("\nContinue previewing? (y/n): ").strip().lower()
                    if response != 'y':
                        print("Preview stopped.")
                        break
                except KeyboardInterrupt:
                    print("\nPreview stopped.")
                    break
            except Exception as e:
                print(f"   Error playing track: {e}")
        
        print("\n" + "="*70)
        print("Preview complete!")
        print("="*70)
    
    def interactive_preview_menu(self, audio_files: List[str]):
        """
        Interactive menu for previewing tracks with more control.
        
        Args:
            audio_files: List of audio file paths
        """
        print("\n" + "="*70)
        print("INTERACTIVE TRACK PREVIEW")
        print("="*70)
        
        while True:
            print("\nOptions:")
            print("1. Preview all tracks (10 seconds each)")
            print("2. Preview all tracks (30 seconds each)")
            print("3. Preview specific track")
            print("4. Preview range of tracks")
            print("5. Skip preview and continue")
            
            choice = input("\nSelect option (1-5): ").strip()
            
            if choice == '1':
                self.preview_tracks(audio_files, preview_seconds=10)
                break
            elif choice == '2':
                self.preview_tracks(audio_files, preview_seconds=30)
                break
            elif choice == '3':
                try:
                    track_num = int(input(f"Enter track number (1-{len(audio_files)}): ").strip())
                    if 1 <= track_num <= len(audio_files):
                        print(f"\nPreviewing track {track_num}...")
                        self.preview_tracks([audio_files[track_num - 1]], preview_seconds=30)
                    else:
                        print("Invalid track number")
                except ValueError:
                    print("Invalid input")
            elif choice == '4':
                try:
                    start = int(input(f"Enter start track (1-{len(audio_files)}): ").strip())
                    end = int(input(f"Enter end track ({start}-{len(audio_files)}): ").strip())
                    if 1 <= start <= end <= len(audio_files):
                        self.preview_tracks(audio_files[start-1:end], preview_seconds=10)
                    else:
                        print("Invalid range")
                except ValueError:
                    print("Invalid input")
            elif choice == '5':
                print("Skipping preview...")
                break
            else:
                print("Invalid option")
    
    def get_audio_duration(self, audio_file: str) -> Optional[float]:
        """
        Get the duration of an audio file in seconds using ffprobe.
        
        Args:
            audio_file: Path to the audio file
            
        Returns:
            Duration in seconds, or None if unable to determine
        """
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_format', audio_file],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                duration = data.get('format', {}).get('duration')
                if duration:
                    return float(duration)
        except Exception as e:
            print(f"Warning: Could not get duration for {audio_file}: {e}")
        
        return None
    
    def calculate_disc_capacity(self, audio_files: List[str], cd_size: int = 80, gaps: Optional[List[float]] = None) -> Dict:
        """
        Calculate total duration and remaining capacity for a list of audio files.
        
        Args:
            audio_files: List of audio file paths
            cd_size: CD size in minutes (74 or 80)
            gaps: Optional list of gap durations to include in calculation
            
        Returns:
            Dictionary with capacity information
        """
        total_seconds = 0
        track_durations = []
        failed_files = []
        
        print("\nCalculating disc capacity...")
        progress = ProgressBar(len(audio_files), prefix='Analyzing:', suffix='', length=40)
        
        for i, audio_file in enumerate(audio_files, 1):
            duration = self.get_audio_duration(audio_file)
            
            track_name = Path(audio_file).name[:30]
            progress.update(i, suffix=track_name)
            
            if duration is not None:
                total_seconds += duration
                track_durations.append({
                    'file': audio_file,
                    'duration': duration
                })
            else:
                failed_files.append(audio_file)
        
        # Add gap time if provided
        gap_time = 0
        if gaps:
            gap_time = sum(gaps)
            total_seconds += gap_time
            print(f"\n  Total gap time: {self._format_time(gap_time)}")
        
        # Determine CD capacity
        cd_capacity = self.CD_80_MIN_SECONDS if cd_size == 80 else self.CD_74_MIN_SECONDS
        remaining_seconds = cd_capacity - total_seconds
        percent_used = (total_seconds / cd_capacity) * 100
        
        return {
            'total_seconds': total_seconds,
            'total_formatted': self._format_time(total_seconds),
            'remaining_seconds': remaining_seconds,
            'remaining_formatted': self._format_time(remaining_seconds) if remaining_seconds > 0 else "0:00",
            'cd_capacity': cd_capacity,
            'cd_size': cd_size,
            'percent_used': percent_used,
            'track_count': len(audio_files),
            'track_durations': track_durations,
            'failed_files': failed_files,
            'fits_on_disc': remaining_seconds >= 0,
            'gap_time': gap_time
        }
    
    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS or HH:MM:SS."""
        td = timedelta(seconds=int(seconds))
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"
    
    def display_capacity_summary(self, capacity_info: Dict):
        """Display a formatted summary of disc capacity."""
        print("\n" + "="*70)
        print("DISC CAPACITY SUMMARY")
        print("="*70)
        
        # Basic info
        print(f"CD Size:        {capacity_info['cd_size']} minutes")
        print(f"Track Count:    {capacity_info['track_count']}")
        print(f"Total Duration: {capacity_info['total_formatted']}")
        
        if capacity_info.get('gap_time', 0) > 0:
            gap_time = capacity_info['gap_time']
            audio_time = capacity_info['total_seconds'] - gap_time
            print(f"  - Audio:      {self._format_time(audio_time)}")
            print(f"  - Gaps:       {self._format_time(gap_time)}")
        
        print(f"Remaining:      {capacity_info['remaining_formatted']}")
        
        # Visual progress bar
        percent = capacity_info['percent_used']
        bar_width = 50
        filled = int(bar_width * percent / 100)
        bar = '█' * filled + '░' * (bar_width - filled)
        print(f"\nUsage: [{bar}] {percent:.1f}%")
        
        # Status indicator
        if capacity_info['fits_on_disc']:
            if percent > 95:
                print("\n⚠ WARNING: Very close to capacity! Consider removing a track.")
            elif percent > 90:
                print("\n✓ Fits on disc, but nearly full.")
            else:
                print("\n✓ Fits comfortably on disc.")
        else:
            overage = abs(capacity_info['remaining_seconds'])
            print(f"\n✗ EXCEEDS CAPACITY by {self._format_time(overage)}!")
            print("   Remove some tracks or use a larger CD.")
        
        # Failed files warning
        if capacity_info['failed_files']:
            print(f"\n⚠ Warning: Could not determine duration for {len(capacity_info['failed_files'])} file(s)")
            print("   These may add additional time to the disc.")
        
        print("="*70)
    
    def _detect_cd_device(self) -> Optional[str]:
        """Detect the CD/DVD writer device."""
        try:
            result = subprocess.run(
                ['wodim', '--devices'],
                capture_output=True,
                text=True
            )
            
            for line in result.stderr.split('\n'):
                if '/dev/' in line:
                    match = re.search(r'(/dev/\S+)', line)
                    if match:
                        device = match.group(1)
                        print(f"Detected CD writer: {device}")
                        return device
                        
            print("No CD writer detected. Using default /dev/sr0")
            return "/dev/sr0"
            
        except FileNotFoundError:
            print("Error: wodim not installed. Please install it first.")
            sys.exit(1)
    
    def parse_m3u_playlist(self, playlist_path: str) -> List[str]:
        """
        Parse an M3U or M3U8 playlist file and extract audio file paths.
        
        Args:
            playlist_path: Path to the .m3u or .m3u8 file
            
        Returns:
            List of audio file paths in playlist order
        """
        audio_files = []
        playlist_file = Path(playlist_path)
        
        if not playlist_file.exists():
            print(f"Error: Playlist file '{playlist_path}' does not exist")
            return []
        
        if playlist_file.suffix.lower() not in ['.m3u', '.m3u8']:
            print(f"Error: '{playlist_path}' is not a valid M3U/M3U8 file")
            return []
        
        print(f"\nParsing playlist: {playlist_path}")
        
        # Determine the base directory for resolving relative paths
        playlist_dir = playlist_file.parent
        
        try:
            # Try UTF-8 first (M3U8 standard), fall back to system encoding
            encodings = ['utf-8', 'latin-1', 'cp1252']
            content = None
            
            for encoding in encodings:
                try:
                    with open(playlist_path, 'r', encoding=encoding) as f:
                        content = f.readlines()
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                print("Error: Could not decode playlist file")
                return []
            
            for line in content:
                line = line.strip()
                
                # Skip empty lines and comments (M3U comments start with #)
                if not line or line.startswith('#'):
                    continue
                
                # Handle both absolute and relative paths
                file_path = Path(line)
                
                # If the path is not absolute, resolve it relative to playlist location
                if not file_path.is_absolute():
                    file_path = playlist_dir / file_path
                
                # Normalize the path
                file_path = file_path.resolve()
                
                # Check if file exists and is an audio file
                if file_path.exists() and file_path.suffix.lower() in self.AUDIO_EXTENSIONS:
                    audio_files.append(str(file_path))
                elif file_path.exists():
                    print(f"⚠ Skipping non-audio file: {file_path.name}")
                else:
                    print(f"⚠ File not found: {file_path}")
            
            if audio_files:
                print(f"✓ Found {len(audio_files)} audio file(s) in playlist")
                print("\nPlaylist order:")
                for i, file in enumerate(audio_files, 1):
                    print(f"  {i}. {Path(file).name}")
            else:
                print("✗ No valid audio files found in playlist")
            
        except Exception as e:
            print(f"Error reading playlist: {e}")
            return []
        
        return audio_files
    
    def scan_folder_for_audio(self, folder_path: str, recursive: bool = True) -> List[str]:
        """
        Scan a folder for audio files.
        
        Args:
            folder_path: Path to the folder to scan
            recursive: If True, scan subdirectories as well
            
        Returns:
            List of audio file paths found
        """
        audio_files = []
        folder = Path(folder_path)
        
        if not folder.exists():
            print(f"Error: Folder '{folder_path}' does not exist")
            return []
        
        if not folder.is_dir():
            print(f"Error: '{folder_path}' is not a directory")
            return []
        
        print(f"\nScanning {'recursively' if recursive else 'non-recursively'}: {folder_path}")
        
        # Choose the appropriate method based on recursive flag
        if recursive:
            pattern = '**/*'
        else:
            pattern = '*'
        
        # Scan for audio files
        for file_path in folder.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in self.AUDIO_EXTENSIONS:
                audio_files.append(str(file_path))
        
       # Sort files naturally (handles numbers in filenames correctly)
        audio_files.sort(key=self._natural_sort_key)
        
        if audio_files:
            print(f"✓ Found {len(audio_files)} audio file(s)")
            print("\nFiles found:")
            for i, file in enumerate(audio_files, 1):
                print(f"  {i}. {Path(file).name}")
        else:
            print("✗ No audio files found in this folder")
        
        return audio_files
    
    def _natural_sort_key(self, path: str) -> List:
        """
        Generate a key for natural sorting (handles numbers in strings correctly).
        Example: ['1.mp3', '2.mp3', '10.mp3'] instead of ['1.mp3', '10.mp3', '2.mp3']
        """
        return [int(text) if text.isdigit() else text.lower()
                for text in re.split(r'(\d+)', path)]
    
    def ask_yes_no_with_help(self, question: str, help_text: str) -> bool:
        """Ask a yes/no question with help option."""
        while True:
            response = input(f"{question} (y/n/?): ").strip().lower()
            
            if response == '?':
                print(f"\n{help_text}\n")
            elif response == 'y':
                return True
            elif response == 'n':
                return False
            else:
                print("Please enter 'y' for yes, 'n' for no, or '?' for help")
    
    def read_audio_cd_tracks(self) -> List[Dict]:
        """Read track information from an audio CD."""
        tracks = []
        
        try:
            # Use cdparanoia to get track info
            result = subprocess.run(
                ['cdparanoia', '-Q'],
                capture_output=True,
                text=True
            )
            
            # Parse track information
            track_pattern = r'^\s*(\d+)\.\s+\d+\s+\[([^\]]+)\]\s+\d+\s+\[([^\]]+)\]'
            
            for line in result.stderr.split('\n'):
                match = re.match(track_pattern, line)
                if match:
                    track_num = int(match.group(1))
                    length = match.group(2)
                    offset = match.group(3)
                    
                    tracks.append({
                        'number': track_num,
                        'length': length,
                        'offset': offset
                    })
            
            return sorted(tracks, key=lambda x: x['number'])
            
        except FileNotFoundError:
            print("cdparanoia not installed. Installing it is recommended for audio CD reading.")
            print("Install with: sudo apt-get install cdparanoia")
            return []
    
    def rip_audio_cd(self, output_dir: str = "./ripped_tracks") -> List[str]:
        """Rip audio CD tracks to WAV files in chronological order."""
        os.makedirs(output_dir, exist_ok=True)
        tracks = self.read_audio_cd_tracks()
        
        if not tracks:
            print("No tracks found on CD")
            return []
        
        ripped_files = []
        
        print("\nRipping audio CD...")
        progress = ProgressBar(len(tracks), prefix='Ripping:', suffix='', length=40)
        
        for i, track in enumerate(tracks, 1):
            track_num = track['number']
            output_file = os.path.join(output_dir, f"track_{track_num:02d}.wav")
            
            progress.update(i, suffix=f'Track {track_num:02d}')
            
            # Use cdparanoia to rip the track
            result = subprocess.run(
                ['cdparanoia', '-w', str(track_num), output_file],
                capture_output=True
            )
            
            if result.returncode == 0:
                ripped_files.append(output_file)
            else:
                pass  # Error already shown by progress bar
        
        return ripped_files
    
    def convert_to_wav(self, input_file: str, output_file: str) -> bool:
        """Convert audio file to WAV format using ffmpeg."""
        try:
            result = subprocess.run(
                ['ffmpeg', '-i', input_file, '-acodec', 'pcm_s16le', 
                 '-ar', '44100', '-ac', '2', output_file, '-y'],
                capture_output=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            print("ffmpeg not installed. Install with: sudo apt-get install ffmpeg")
            return False
    
    def convert_audio_format(self, input_file: str, output_file: str, 
                            format_type: str, quality: str = 'high') -> bool:
        """
        Convert audio file to specified format using ffmpeg.
        
        Args:
            input_file: Path to input audio file
            output_file: Path to output audio file
            format_type: Target format ('mp3', 'flac', 'ogg', 'aac', 'm4a', 'opus', 'wav')
            quality: Quality setting ('low', 'medium', 'high', 'lossless')
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Base command
            cmd = ['ffmpeg', '-i', input_file]
            
            # Format-specific encoding settings
            if format_type.lower() == 'mp3':
                if quality == 'low':
                    cmd.extend(['-codec:a', 'libmp3lame', '-b:a', '128k'])
                elif quality == 'medium':
                    cmd.extend(['-codec:a', 'libmp3lame', '-b:a', '192k'])
                elif quality == 'high':
                    cmd.extend(['-codec:a', 'libmp3lame', '-b:a', '320k'])
                else:  # lossless (V0)
                    cmd.extend(['-codec:a', 'libmp3lame', '-q:a', '0'])
            
            elif format_type.lower() == 'flac':
                # FLAC is always lossless
                cmd.extend(['-codec:a', 'flac'])
                if quality == 'high' or quality == 'lossless':
                    cmd.extend(['-compression_level', '8'])
                else:
                    cmd.extend(['-compression_level', '5'])
            
            elif format_type.lower() in ['ogg', 'vorbis']:
                if quality == 'low':
                    cmd.extend(['-codec:a', 'libvorbis', '-q:a', '3'])
                elif quality == 'medium':
                    cmd.extend(['-codec:a', 'libvorbis', '-q:a', '5'])
                elif quality == 'high':
                    cmd.extend(['-codec:a', 'libvorbis', '-q:a', '7'])
                else:  # lossless/max
                    cmd.extend(['-codec:a', 'libvorbis', '-q:a', '10'])
            
            elif format_type.lower() in ['aac', 'm4a']:
                if quality == 'low':
                    cmd.extend(['-codec:a', 'aac', '-b:a', '128k'])
                elif quality == 'medium':
                    cmd.extend(['-codec:a', 'aac', '-b:a', '192k'])
                elif quality == 'high':
                    cmd.extend(['-codec:a', 'aac', '-b:a', '256k'])
                else:  # max
                    cmd.extend(['-codec:a', 'aac', '-b:a', '320k'])
            
            elif format_type.lower() == 'opus':
                if quality == 'low':
                    cmd.extend(['-codec:a', 'libopus', '-b:a', '96k'])
                elif quality == 'medium':
                    cmd.extend(['-codec:a', 'libopus', '-b:a', '128k'])
                elif quality == 'high':
                    cmd.extend(['-codec:a', 'libopus', '-b:a', '192k'])
                else:  # max
                    cmd.extend(['-codec:a', 'libopus', '-b:a', '256k'])
            
            elif format_type.lower() == 'wav':
                cmd.extend(['-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2'])
            
            else:
                print(f"Unsupported format: {format_type}")
                return False
            
            # Add output file and overwrite flag
            cmd.extend([output_file, '-y'])
            
            # Run conversion
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"Error converting to {format_type}: {result.stderr}")
                return False
            
            return True
            
        except FileNotFoundError:
            print("ffmpeg not installed. Install with: sudo apt-get install ffmpeg")
            return False
        except Exception as e:
            print(f"Error during conversion: {e}")
            return False
    
    def batch_convert_formats(self, input_files: List[str], output_dir: str,
                             formats: List[str], quality: str = 'high',
                             preserve_metadata: bool = True) -> Dict[str, List[str]]:
        """
        Batch convert audio files to multiple formats.
        
        Args:
            input_files: List of input audio file paths
            output_dir: Directory to save converted files
            formats: List of target formats (e.g., ['mp3', 'flac', 'ogg'])
            quality: Quality setting for conversions
            preserve_metadata: Whether to copy metadata to converted files
            
        Returns:
            Dictionary mapping format to list of successfully converted files
        """
        os.makedirs(output_dir, exist_ok=True)
        results = {fmt: [] for fmt in formats}
        
        print("\n" + "="*70)
        print("BATCH FORMAT CONVERSION")
        print("="*70)
        print(f"\nConverting {len(input_files)} file(s) to {len(formats)} format(s)")
        print(f"Quality: {quality.upper()}")
        print(f"Output directory: {output_dir}")
        print("-"*70)
        
        total_conversions = len(input_files) * len(formats)
        current = 0
        
        progress = ProgressBar(total_conversions, prefix='Converting:', suffix='', length=50)
        
        for input_file in input_files:
            if not os.path.exists(input_file):
                print(f"\n✗ File not found: {input_file}")
                current += len(formats)
                continue
            
            base_name = Path(input_file).stem
            
            for fmt in formats:
                current += 1
                
                # Determine output extension
                if fmt.lower() in ['ogg', 'vorbis']:
                    ext = 'ogg'
                elif fmt.lower() == 'aac':
                    ext = 'm4a'  # AAC usually in M4A container
                else:
                    ext = fmt.lower()
                
                output_file = os.path.join(output_dir, f"{base_name}.{ext}")
                
                # Update progress bar
                progress_text = f"{Path(input_file).name[:20]} → {fmt.upper()}"
                progress.update(current, suffix=progress_text)
                
                if self.convert_audio_format(input_file, output_file, fmt, quality):
                    results[fmt].append(output_file)
        
        # Summary
        print("\n" + "="*70)
        print("CONVERSION SUMMARY")
        print("="*70)
        
        for fmt, files in results.items():
            success_count = len(files)
            total_count = len(input_files)
            print(f"\n{fmt.upper()}:")
            print(f"  Successfully converted: {success_count}/{total_count}")
            
            if files:
                total_size = sum(os.path.getsize(f) for f in files) / (1024 * 1024)
                print(f"  Total size: {total_size:.2f} MB")
        
        print("="*70)
        
        return results
    
    def export_formats_interactive(self, input_files: List[str]):
        """
        Interactive menu for exporting audio files to multiple formats.
        
        Args:
            input_files: List of input audio file paths
        """
        print("\n" + "="*70)
        print("MULTIPLE FORMAT EXPORT")
        print("="*70)
        print(f"\nReady to export {len(input_files)} file(s)")
        
        # Select output directory
        print("\nOutput directory:")
        print("1. Current directory")
        print("2. Create new subdirectory")
        print("3. Specify custom path")
        
        dir_choice = input("\nSelect option (1-3): ").strip()
        
        if dir_choice == '1':
            output_dir = os.getcwd()
        elif dir_choice == '2':
            subdir_name = input("Enter subdirectory name (e.g., 'converted'): ").strip()
            if not subdir_name:
                subdir_name = 'converted'
            output_dir = os.path.join(os.getcwd(), subdir_name)
        elif dir_choice == '3':
            output_dir = input("Enter full output path: ").strip()
        else:
            print("Invalid choice, using current directory")
            output_dir = os.getcwd()
        
        # Select formats
        print("\n" + "-"*70)
        print("SELECT EXPORT FORMATS")
        print("-"*70)
        print("\nAvailable formats:")
        print("1. MP3 (Most compatible, lossy)")
        print("2. FLAC (Lossless, larger files)")
        print("3. OGG Vorbis (Open source, lossy)")
        print("4. AAC/M4A (Apple/iTunes, lossy)")
        print("5. Opus (Modern, efficient, lossy)")
        print("6. WAV (Uncompressed, CD quality)")
        print("7. All lossy formats (MP3 + OGG + AAC + Opus)")
        print("8. All formats")
        print("9. Custom selection")
        
        format_choice = input("\nSelect option (1-9): ").strip()
        
        if format_choice == '1':
            formats = ['mp3']
        elif format_choice == '2':
            formats = ['flac']
        elif format_choice == '3':
            formats = ['ogg']
        elif format_choice == '4':
            formats = ['aac']
        elif format_choice == '5':
            formats = ['opus']
        elif format_choice == '6':
            formats = ['wav']
        elif format_choice == '7':
            formats = ['mp3', 'ogg', 'aac', 'opus']
        elif format_choice == '8':
            formats = ['mp3', 'flac', 'ogg', 'aac', 'opus', 'wav']
        elif format_choice == '9':
            print("\nEnter format codes separated by commas:")
            print("(mp3, flac, ogg, aac, opus, wav)")
            formats_input = input("Formats: ").strip().lower()
            formats = [f.strip() for f in formats_input.split(',')]
        else:
            print("Invalid choice, using MP3")
            formats = ['mp3']
        
        # Select quality
        print("\n" + "-"*70)
        print("SELECT QUALITY")
        print("-"*70)
        print("\nQuality presets:")
        print("1. Low (Smaller files, lower quality)")
        print("2. Medium (Balanced)")
        print("3. High (Larger files, better quality) [Recommended]")
        print("4. Maximum/Lossless (Largest files, best quality)")
        
        quality_choice = input("\nSelect quality (1-4): ").strip()
        
        if quality_choice == '1':
            quality = 'low'
        elif quality_choice == '2':
            quality = 'medium'
        elif quality_choice == '3':
            quality = 'high'
        elif quality_choice == '4':
            quality = 'lossless'
        else:
            print("Invalid choice, using high quality")
            quality = 'high'
        
        # Confirm and proceed
        print("\n" + "-"*70)
        print("EXPORT SUMMARY")
        print("-"*70)
        print(f"Input files:    {len(input_files)}")
        print(f"Output formats: {', '.join(f.upper() for f in formats)}")
        print(f"Quality:        {quality.upper()}")
        print(f"Output dir:     {output_dir}")
        print(f"Total exports:  {len(input_files) * len(formats)}")
        
        confirm = input("\nProceed with export? (y/n): ").strip().lower()
        
        if confirm == 'y':
            results = self.batch_convert_formats(
                input_files, output_dir, formats, quality
            )
            
            print("\n✓ Export complete!")
            print(f"\nFiles saved to: {output_dir}")
        else:
            print("\nExport cancelled.")
    
    def burn_audio_cd(self, audio_files: List[str], normalize: bool = True, speed: int = 8, 
                 dry_run: bool = False, use_cdtext: bool = True, 
                 track_gaps: Optional[List[float]] = None,
                 fade_ins: Optional[List[float]] = None,
                 fade_outs: Optional[List[float]] = None,
                 multi_session: bool = False,
                 finalize: bool = True) -> bool:
        """
        Burn audio files to CD in the specified order with optional CD-TEXT, custom gaps, and fades.
        
        Args:
            audio_files: List of audio file paths
            normalize: Whether to normalize audio levels
            speed: Burn speed
            dry_run: If True, simulate burning without actually writing
            use_cdtext: If True, embed CD-TEXT metadata
            track_gaps: Optional list of gap durations (seconds) for each track
            fade_ins: Optional list of fade in durations (seconds) for each track
            fade_outs: Optional list of fade out durations (seconds) for each track
            
        Returns:
            True if successful, False otherwise
        """
        
        if dry_run:
            print("\n" + "="*70)
            print("DRY RUN MODE - NO ACTUAL BURNING WILL OCCUR")
            print("="*70)
        
        if multi_session:
            print("\n" + "="*70)
            print("MULTI-SESSION MODE - ADDING TRACKS TO EXISTING DISC")
            if not finalize:
                print("Disc will remain OPEN for future sessions")
            else:
                print("Disc will be FINALIZED after this session")
            print("="*70)
        
        # Ensure files are sorted by track number if they have track numbers in filename
        def extract_track_number(filename: str) -> int:
            match = re.search(r'track[_\s]*(\d+)', filename, re.IGNORECASE)
            if match:
                return int(match.group(1))
            match = re.search(r'^(\d+)', os.path.basename(filename))
            if match:
                return int(match.group(1))
            return 999  # Put unnumbered files at the end
        
        # Sort files by track number
        audio_files_sorted = sorted(audio_files, key=extract_track_number)
        
        print("\nTrack order for burning:")
        for i, file in enumerate(audio_files_sorted, 1):
            print(f"  Track {i}: {os.path.basename(file)}")
        
        # Use defaults if not specified
        if track_gaps is None:
            track_gaps = [self.DEFAULT_GAP_SECONDS] * len(audio_files_sorted)
        if fade_ins is None:
            fade_ins = [self.DEFAULT_FADE_IN] * len(audio_files_sorted)
        if fade_outs is None:
            fade_outs = [self.DEFAULT_FADE_OUT] * len(audio_files_sorted)
        
        # Extract metadata for CD-TEXT
        tracks_metadata = []
        album_info = {
            'title': 'Audio CD',
            'artist': 'Various Artists',
            'genre': '',
            'date': ''
        }
        
        if use_cdtext:
            print("\n" + "="*70)
            print("EXTRACTING METADATA FOR CD-TEXT")
            print("="*70)
            
            # ask if user wants to try online lookup first
            lookup_response = input("\nTry online database lookup (CDDB/MusicBrainz)? (y/n): ").strip().lower()
            
            if lookup_response == 'y':
                # we need to convert files to WAV first for disc ID calculation
                print("\nPreparing files for disc identification...")
                temp_wav_files = []
                with tempfile.TemporaryDirectory() as lookup_temp_dir:
                    prep_progress = ProgressBar(len(audio_files_sorted), prefix='Preparing:', suffix='', length=40)
                    for i, audio_file in enumerate(audio_files_sorted, 1):
                        temp_wav = os.path.join(lookup_temp_dir, f"temp_{i:02d}.wav")
                        track_name = Path(audio_file).name[:25]
                        prep_progress.update(i, suffix=track_name)
                        if self.convert_to_wav(audio_file, temp_wav):
                            temp_wav_files.append(temp_wav)
                    
                    if temp_wav_files:
                        # attempt online lookup
                        online_metadata = self.lookup_cd_metadata(temp_wav_files)
                        
                        if online_metadata:
                            # apply the looked-up metadata
                            tracks_metadata, album_info = self.apply_lookup_metadata(
                                online_metadata, audio_files_sorted
                            )
                            print("\n✓ Using metadata from online database")
                        else:
                            print("\n✗ No matches found online, falling back to file metadata")
                            # fall back to extracting from files
                            meta_progress = ProgressBar(len(audio_files_sorted), prefix='Reading metadata:', suffix='', length=40)
                            for i, audio_file in enumerate(audio_files_sorted, 1):
                                track_name = Path(audio_file).name[:30]
                                meta_progress.update(i, suffix=track_name)
                                metadata = self.extract_metadata(audio_file)
                                tracks_metadata.append(metadata)
                                
                                if i == 1:
                                    album_info['title'] = metadata.get('album', 'Audio CD')
                                    album_info['artist'] = metadata.get('artist', 'Various Artists')
                                    album_info['genre'] = metadata.get('genre', '')
                                    album_info['date'] = metadata.get('date', '')
                    else:
                        print("✗ Could not prepare files for lookup, using file metadata")
                        for i, audio_file in enumerate(audio_files_sorted, 1):
                            print(f"Reading metadata from track {i}...")
                            metadata = self.extract_metadata(audio_file)
                            tracks_metadata.append(metadata)
                            
                            if i == 1:
                                album_info['title'] = metadata.get('album', 'Audio CD')
                                album_info['artist'] = metadata.get('artist', 'Various Artists')
                                album_info['genre'] = metadata.get('genre', '')
                                album_info['date'] = metadata.get('date', '')
            else:
                # extract metadata from files
                for i, audio_file in enumerate(audio_files_sorted, 1):
                    print(f"Reading metadata from track {i}...")
                    metadata = self.extract_metadata(audio_file)
                    tracks_metadata.append(metadata)
                    
                    # try to determine album info from first track
                    if i == 1:
                        album_info['title'] = metadata.get('album', 'Audio CD')
                        album_info['artist'] = metadata.get('artist', 'Various Artists')
                        album_info['genre'] = metadata.get('genre', '')
                        album_info['date'] = metadata.get('date', '')
            
            # Display CD-TEXT preview
            self.display_cdtext_preview(tracks_metadata, album_info)
            
            # Ask if user wants to edit metadata
            edit_response = input("\nEdit CD-TEXT metadata? (y/n): ").strip().lower()
            if edit_response == 'y':
                tracks_metadata, album_info = self.edit_cdtext_metadata(tracks_metadata, album_info)
                # Show updated preview
                self.display_cdtext_preview(tracks_metadata, album_info)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_files = []
            checksums = {}  # Store checksums for verification
            
            # Convert all files to WAV format WITH FADES
            print("\n" + ("="*70 if not dry_run else ""))
            print("STEP 1: Converting files to WAV format and applying fades" + (" (simulated)" if dry_run else ""))
            print("="*70)
            
            if not dry_run:
                progress = ProgressBar(len(audio_files_sorted), prefix='Converting:', suffix='', length=40)
            
            for i, audio_file in enumerate(audio_files_sorted, 1):
                if not os.path.exists(audio_file):
                    print(f"Warning: File not found: {audio_file}")
                    continue
                
                fade_in = fade_ins[i-1] if i <= len(fade_ins) else 0.0
                fade_out = fade_outs[i-1] if i <= len(fade_outs) else 0.0
                
                # Always convert and apply fades (even if 0)
                wav_output = os.path.join(temp_dir, f"track_{i:02d}.wav")
                
                fade_desc = []
                if fade_in > 0:
                    fade_desc.append(f"↗{fade_in}s in")
                if fade_out > 0:
                    fade_desc.append(f"↘{fade_out}s out")
                
                if fade_desc:
                    if dry_run:
                        print(f"[DRY RUN] Track {i}: {os.path.basename(audio_file)}")
                        print(f"  Converting with fades: {', '.join(fade_desc)}")
                else:
                    if dry_run:
                        print(f"[DRY RUN] Track {i}: {os.path.basename(audio_file)}")
                        print(f"  Converting (no fades)")
                
                if not dry_run:
                    # Update progress bar
                    track_name = os.path.basename(audio_file)[:30]
                    progress.update(i, suffix=f'{track_name}')
                    
                    if self.apply_fade_effects(audio_file, wav_output, fade_in, fade_out):
                        wav_files.append(wav_output)
                        
                        # Calculate checksum for later verification
                        checksum = self.calculate_file_checksum(wav_output, 'sha256')
                        if checksum:
                            checksums[wav_output] = checksum
                    else:
                        pass  # Error shown in apply_fade_effects
                else:
                    # In dry run, simulate successful conversion
                    wav_files.append(wav_output)
            
            if not wav_files:
                print("No valid audio files to burn")
                return False
            
            # Store for verification
            self.last_burn_wav_files = wav_files.copy()
            self.last_burn_checksums = checksums.copy()
            
            # Normalize audio if requested
            if normalize:
                print("\n" + "="*70)
                print("STEP 2: Normalizing audio levels" + (" (simulated)" if dry_run else ""))
                print("="*70)
                
                normalized_files = []
                
                if not dry_run:
                    norm_progress = ProgressBar(len(wav_files), prefix='Normalizing:', suffix='', length=40)
                
                for i, wav_file in enumerate(wav_files, 1):
                    norm_output = os.path.join(temp_dir, f"norm_track_{i:02d}.wav")
                    
                    if dry_run:
                        print(f"[DRY RUN] Would normalize track {i}: {os.path.basename(wav_file)}")
                        normalized_files.append(wav_file)
                    else:
                        track_name = os.path.basename(wav_file)[:30]
                        norm_progress.update(i, suffix=f'{track_name}')
                        
                        # Use sox for normalization
                        result = subprocess.run(
                            ['sox', wav_file, norm_output, 'norm'],
                            capture_output=True
                        )
                        
                        if result.returncode == 0:
                            normalized_files.append(norm_output)
                            
                            # Recalculate checksum after normalization
                            checksum = self.calculate_file_checksum(norm_output, 'sha256')
                            if checksum:
                                # Update checksum with normalized version
                                checksums[norm_output] = checksum
                                # Remove old checksum
                                if wav_file in checksums:
                                    del checksums[wav_file]
                        else:
                            normalized_files.append(wav_file)  # Use original if norm fails
                
                wav_files = normalized_files
                self.last_burn_wav_files = wav_files.copy()
                self.last_burn_checksums = checksums.copy()
            else:
                print("\n" + "="*70)
                print("STEP 2: Skipping normalization (not requested)")
                print("="*70)
            
            # Create TOC file for cdrdao
            print("\n" + "="*70)
            step3_desc = "STEP 3: Creating TOC file"
            if use_cdtext:
                step3_desc += " with CD-TEXT"
            step3_desc += " and custom gaps"
            if dry_run:
                step3_desc += " (simulated)"
            print(step3_desc)
            print("="*70)
            
            toc_file = os.path.join(temp_dir, "audio.toc")
            
            if not dry_run:
                if use_cdtext:
                    self.generate_toc_with_cdtext(wav_files, tracks_metadata, album_info, toc_file, track_gaps)
                    print(f"✓ TOC file with CD-TEXT and custom gaps created: {toc_file}")
                else:
                    # Generate TOC without CD-TEXT but with custom gaps
                    with open(toc_file, 'w') as f:
                        f.write("CD_DA\n\n")
                        
                        for i, (wav_file, gap) in enumerate(zip(wav_files, track_gaps), 1):
                            f.write(f"// Track {i}\n")
                            f.write("TRACK AUDIO\n")
                            
                            # Pregap (silence before track)
                            if gap > 0:
                                gap_msf = self.frames_to_msf(self.frames_from_seconds(gap))
                                f.write(f"PREGAP {gap_msf}\n")
                            
                            f.write(f'FILE "{wav_file}" 0\n\n')
                    print(f"✓ TOC file with custom gaps created: {toc_file}")
            else:
                print("[DRY RUN] TOC file contents that would be created:")
                print("─"*70)
                if use_cdtext:
                    print("CD_DA\n")
                    print("CD_TEXT {")
                    print(f'  Album: "{album_info["title"]}"')
                    print(f'  Artist: "{album_info["artist"]}"')
                    print("}\n")
                    for i, (metadata, gap, fade_in, fade_out) in enumerate(zip(tracks_metadata, track_gaps, fade_ins, fade_outs), 1):
                        print(f"Track {i}: {metadata['title']} - {metadata['performer']}")
                        if gap > 0:
                            print(f"  Pregap: {gap}s")
                        if fade_in > 0 or fade_out > 0:
                            fades = []
                            if fade_in > 0:
                                fades.append(f"{fade_in}s fade in")
                            if fade_out > 0:
                                fades.append(f"{fade_out}s fade out")
                            print(f"  Fades: {', '.join(fades)}")
                else:
                    print("CD_DA (no CD-TEXT)\n")
                    for i, (wav_file, gap, fade_in, fade_out) in enumerate(zip(wav_files, track_gaps, fade_ins, fade_outs), 1):
                        print(f"Track {i}: {os.path.basename(wav_file)}")
                        if gap > 0:
                            print(f"  Pregap: {gap}s")
                        if fade_in > 0 or fade_out > 0:
                            fades = []
                            if fade_in > 0:
                                fades.append(f"{fade_in}s fade in")
                            if fade_out > 0:
                                fades.append(f"{fade_out}s fade out")
                            print(f"  Fades: {', '.join(fades)}")
                print("─"*70)
            
            # Burn using cdrdao for precise track control
            print("\n" + "="*70)
            print(f"STEP 4: Burning to CD at {speed}x speed" + (" (simulated)" if dry_run else ""))
            print("="*70)
            
            if dry_run:
                print(f"\n[DRY RUN] Command that would be executed:")
                print(f"  cdrdao write --device {self.device} --speed {speed} --eject {toc_file}")
                if use_cdtext:
                    print("\n✓ CD-TEXT would be embedded in the disc")
                print(f"✓ Custom track gaps would be applied")
                
                # Count fades
                fade_count = sum(1 for f_in, f_out in zip(fade_ins, fade_outs) if f_in > 0 or f_out > 0)
                if fade_count > 0:
                    print(f"✓ Fade effects applied to {fade_count} track(s)")
                
                print("\n✓ DRY RUN COMPLETE - No CD was burned")
                print("="*70)
                return True
            
            print("\nInserting blank CD and starting burn process...")
            print("Please wait, this may take several minutes...\n")
            
            burn_cmd = [
                'cdrdao', 'write',
                '--device', self.device,
                '--speed', str(speed)
            ]

            if multi_session and not finalize:
                burn_cmd.append('--multi')

            burn_cmd.extend(['--eject', toc_file])
            
            result = subprocess.run(burn_cmd)
            
            if result.returncode != 0:
                print("\nWarning: cdrdao failed or CD-TEXT not supported.")
                print("Trying alternative burn method with wodim (without CD-TEXT and custom gaps)...")
                burn_cmd = [
                    'wodim',
                    f'dev={self.device}',
                    '-v',
                    '-audio',
                    '-pad',
                    f'speed={speed}'
                ]
                
                if multi_session and not finalize:
                    burn_cmd.append('-multi')
                
                burn_cmd.append('-eject')
                burn_cmd.extend(wav_files)
                
                result = subprocess.run(burn_cmd)

            return result.returncode == 0
    
    def create_cue_sheet(self, audio_files: List[str], output_file: str = "audio.cue"):
        """Create a CUE sheet for the audio files."""
        with open(output_file, 'w') as f:
            f.write('TITLE "Audio CD"\n')
            f.write('PERFORMER "Various Artists"\n\n')
            
            for i, audio_file in enumerate(audio_files, 1):
                filename = os.path.basename(audio_file)
                f.write(f'FILE "{filename}" WAVE\n')
                f.write(f'  TRACK {i:02d} AUDIO\n')
                f.write(f'    TITLE "Track {i}"\n')
                f.write(f'    PERFORMER "Unknown Artist"\n')
                f.write(f'    INDEX 01 00:00:00\n\n')
        
        print(f"CUE sheet created: {output_file}")

class MusicCDOrganizer:
    """Helper class to organize music files with metadata."""
    
    def __init__(self):
        self.writer = AudioCDWriter()
    
    def read_metadata(self, audio_file: str) -> Dict:
        """Read metadata from audio file using ffprobe."""
        return self.writer.extract_metadata(audio_file)
    
    def organize_by_track_number(self, audio_files: List[str]) -> List[str]:
        """Organize audio files by their metadata track number."""
        files_with_metadata = []
        
        for file in audio_files:
            metadata = self.read_metadata(file)
            
            # Extract track number
            track_str = metadata['track']
            if '/' in track_str:  # Format: "3/12"
                track_num = int(track_str.split('/')[0])
            else:
                try:
                    track_num = int(track_str)
                except:
                    track_num = 999
            
            files_with_metadata.append({
                'file': file,
                'track_number': track_num,
                'metadata': metadata
            })
        
        # Sort by track number
        sorted_files = sorted(files_with_metadata, key=lambda x: x['track_number'])
        
        print("\nOrganized track list:")
        for item in sorted_files:
            track = item['track_number'] if item['track_number'] != 999 else '?'
            title = item['metadata']['title']
            artist = item['metadata']['artist']
            print(f"  Track {track}: {artist} - {title}")
        
        return [item['file'] for item in sorted_files]

class HelpSystem:
    """Provides contextual help for various options."""

    @staticmethod
    def verification_help():
        return """
═══════════════════════════════════════════════════════════════════════
CD VERIFICATION EXPLAINED
═══════════════════════════════════════════════════════════════════════

CD verification reads back the burned disc and compares it to the
original data to ensure a perfect, error-free burn. This is crucial
for archival and important recordings.

WHY VERIFY?
✓ Ensures 100% accurate burn
✓ Detects write errors or bad media
✓ Confirms disc is readable
✓ Peace of mind for important recordings
✓ Identifies problems before it's too late

VERIFICATION METHODS:

1. QUICK VERIFICATION (30 seconds)
   What it checks:
   • Can the disc be read at all?
   • Does it have the correct number of tracks?
   
   Use when:
   ✓ You just want basic confirmation
   ✓ Time is limited
   ✓ Using high-quality media
   ✓ Not critical data
   
   Limitations:
   ✗ Doesn't verify actual audio data
   ✗ Won't detect bit errors
   ✗ May miss corrupted tracks

2. STANDARD VERIFICATION (1-2 minutes)
   What it checks:
   • Disc readability
   • Correct track count
   • Track durations match originals
   
   Use when:
   ✓ Good balance of speed and thoroughness
   ✓ Want reasonable confidence
   ✓ Creating personal mixes
   ✓ Non-archival purposes
   
   Limitations:
   ✗ Doesn't verify bit-perfect accuracy
   ✗ May miss subtle corruption
   ✗ Duration matching has tolerance

3. FULL BIT-PERFECT VERIFICATION (5-10 minutes)
   What it checks:
   • Disc readability
   • Correct track count
   • EVERY SINGLE BIT of audio data
   • Uses SHA-256 checksums
   
   How it works:
   1. Rips each track back from the CD
   2. Calculates cryptographic checksum
   3. Compares to original pre-burn checksum
   4. Reports any discrepancies
   
   Use when:
   ✓ Archival/master recordings
   ✓ Important audio (live recordings, etc.)
   ✓ Using untested/cheap media
   ✓ Maximum confidence needed
   ✓ Creating backup/safety copies
   
   Advantages:
   ✓ 100% certainty of perfect burn
   ✓ Detects ANY data corruption
   ✓ Mathematically proven accuracy
   ✓ Industry-standard verification

TECHNICAL DETAILS:

Checksums (SHA-256):
• Cryptographic hash of audio data
• Any change produces different checksum
• Calculated before burn and after rip
• If checksums match = bit-perfect copy

Why Bit-Perfect Matters:
• Audio CDs have no error correction metadata
• Single bit flip can cause audible glitch
• Over time, marginal burns degrade faster
• Perfect burn = longer disc life

Verification Process:
1. Original WAV files have checksums calculated
2. CD is burned
3. Tracks are ripped back from CD
4. Ripped files have checksums calculated
5. Checksums compared for each track

Requirements:
• cdparanoia (for ripping verification)
• Few extra minutes of time
• The burned CD still in the drive

WHEN VERIFICATION FAILS:

Common causes:
• Dirty or scratched blank CD
• Low-quality media
• Drive needs cleaning
• Burn speed too high
• Mechanical issues with drive

What to do:
1. Try burning again at lower speed (4x or 2x)
2. Use different/better quality CD-R
3. Clean CD burner lens
4. Try different blank CD brand
5. Check drive health

VERIFICATION BEST PRACTICES:

For personal mixes/casual use:
→ Standard verification is usually fine

For important recordings:
→ Always use Full verification

For archival/master copies:
→ Full verification is MANDATORY
→ Also verify periodically (yearly)
→ Consider burning 2 copies

For time-sensitive situations:
→ Quick verification acceptable
→ Can verify more thoroughly later

SKIP VERIFICATION:
Only skip if:
• Testing/experimental burns
• Very trusted setup (good media, clean drive)
• Non-important content
• Time extremely limited

⚠ WARNING: Skipping verification means you won't know if the burn
failed until you try to play it later (possibly years from now).

VERIFICATION STATISTICS:
• ~95% of burns are perfect without verification
• ~4% have minor issues caught by standard verification
• ~1% have serious issues only caught by full verification

That 1% matters when it's YOUR important recording!

COMPARISON TO COMMERCIAL CDs:
Commercial CDs are:
• Pressed (not burned) - more reliable
• Verified during manufacturing
• Made with better quality materials

Your burned CDs benefit MORE from verification because:
• Write-once media is more error-prone
• Home burners less precise than factory equipment
• Media quality varies widely

RECOMMENDATION:
Default to STANDARD verification for most uses.
Use FULL verification for anything you'd be upset to lose.

Time investment:
• 2 minutes now vs. hours re-burning later
• Small price for peace of mind
• Catches problems while you can still fix them

TIP: The burning process typically takes 10-15 minutes anyway.
     Adding 2-5 minutes for verification is well worth it!

═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def fade_effects_help():
        return """
═══════════════════════════════════════════════════════════════════════
FADE IN/OUT EFFECTS EXPLAINED
═══════════════════════════════════════════════════════════════════════

Fade effects gradually increase (fade in) or decrease (fade out) the
volume at the beginning or end of tracks, creating smooth, professional
transitions.

WHAT ARE FADES?
• FADE IN: Track starts at silence and gradually increases to full volume
• FADE OUT: Track gradually decreases from full volume to silence at end
• Creates smooth, polished transitions
• Professional audio production technique

WHY USE FADES?
✓ Smooth album flow between different songs
✓ Remove abrupt starts or endings
✓ Create radio-style presentations
✓ DJ mix preparation (short fades for crossfading)
✓ Hide recording artifacts at track beginnings/endings
✓ Professional sound for home-produced tracks

FADE TYPES:

1. NO FADES (0 seconds)
   • Audio plays exactly as-is
   • Use for professionally mastered albums
   • When tracks already have natural fades
   • Classical music with precise timings

2. STANDARD FADE OUT (3 seconds)
   • Most common approach
   • Radio-style presentation
   • Good for ending tracks smoothly
   • No fade in (preserves song intros)

3. FADE IN/OUT (2s in, 3s out)
   • Complete smooth transitions
   • Good for compilation CDs
   • Reduces jarring transitions
   • Professional mixtape sound

4. DJ MIX FADES (0.5 seconds)
   • Short fades for beatmatching
   • Designed to overlap with adjacent tracks
   • Seamless mix capability
   • EDM, dance music compilations

5. GENTLE FADES (3-5 seconds)
   • Longer, more gradual transitions
   • Relaxation/meditation CDs
   • Ambient music
   • Audiobook chapters

6. RADIO-STYLE (4s fade out only)
   • Preserves intros, smooths endings
   • Mimics commercial radio
   • Good for talk/music mixes
   • Professional broadcast sound

TECHNICAL DETAILS:
• Fades use linear volume curves (even, natural sound)
• Applied during WAV conversion (before burning)
• Permanent in the burned CD
• Original files remain unchanged
• Uses ffmpeg's afade filter

FADE DURATION GUIDELINES:
• 0.5s - Very short (DJ crossfade prep)
• 1-2s - Quick, subtle fade
• 3-4s - Standard professional fade
• 5-7s - Long, gentle fade
• 8-10s - Very long (special effects)

WHEN TO USE FADE IN:
✓ Track has abrupt/harsh beginning
✓ Recording has noise at start
✓ Creating smooth album flow
✓ DJ mix preparation
✗ Song has iconic intro (don't fade!)
✗ Classical music (preserve conductor's timing)

WHEN TO USE FADE OUT:
✓ Track ends abruptly
✓ Want smooth ending
✓ Radio-style presentation
✓ Hiding recording artifacts
✗ Song has dramatic ending (preserve it!)
✗ Live recordings (keep natural applause)

COMMON PRESETS:

Standard Fade Out (3s out, 0s in)
├─ Best for: General purpose, most albums
├─ Preserves: Song intros
└─ Effect: Professional, smooth endings

Full Fades (2s in, 3s out)
├─ Best for: Mixtapes, compilations
├─ Effect: Complete smooth flow
└─ Use when: Mixing different sources

DJ Mix (0.5s in/out)
├─ Best for: Electronic music, dance
├─ Effect: Beatmatch-ready
└─ Purpose: For manual mixing/crossfading

Radio Style (4s out, 0s in)
├─ Best for: Broadcast-style CDs
├─ Effect: Like commercial radio
└─ Good for: Talk/music combinations

Gentle (3s in, 5s out)
├─ Best for: Ambient, meditation
├─ Effect: Very smooth, relaxing
└─ Purpose: Calm, unobtrusive transitions

INDIVIDUAL TRACK CONTROL:
You can set different fades for each track:
• Track 1: 0s in, 3s out (preserve intro, smooth ending)
• Track 2-9: 0s in, 3s out (normal tracks)
• Track 10: 0s in, 8s out (final track, long fade)

COMBINING WITH OTHER FEATURES:
• Works with CD-TEXT (metadata preserved)
• Works with custom gaps (fades + pauses = pro sound)
• Works with normalization (apply fades first, then normalize)
• Compatible with all audio formats

IMPORTANT NOTES:
• Fades are PERMANENT in the burned CD
• Cannot be removed after burning
• Original source files remain unchanged
• Preview tracks before burning to test fade durations
• Fades reduce effective track length slightly

AUDIO QUALITY:
• No quality loss (uses high-quality algorithms)
• Linear fades sound natural
• No digital artifacts
• Professional broadcast-quality

TROUBLESHOOTING:
• If fade is too short: Song still ends abruptly
  → Increase fade duration to 4-5 seconds
  
• If fade is too long: Noticeable volume drop
  → Reduce fade duration to 2-3 seconds
  
• If fade sounds unnatural: Wrong type of content
  → Use no fade for classical/live recordings

BEST PRACTICES:
1. Listen to your tracks first
2. Identify which need fades (abrupt starts/ends)
3. Start with standard presets
4. Customize individual tracks if needed
5. Preview before burning (if possible)
6. Remember: Less is often more!

EXAMPLES:

Album Mix CD:
- Track 1 (upbeat): 0s in, 3s out
- Track 2 (ballad): 2s in, 4s out
- Track 3 (rock): 0s in, 3s out
- Track 4 (acoustic): 2s in, 5s out

DJ Mix Preparation:
- All tracks: 0.5s in, 0.5s out
- Allows beatmatching overlap
- Seamless when mixed properly

Meditation/Sleep CD:
- All tracks: 3s in, 5s out
- Very gentle transitions
- Maintains relaxed atmosphere

Radio Show:
- Speech tracks: 0s in, 0s out
- Music tracks: 0s in, 4s out
- Preserves speech clarity

TIP: When in doubt, use "Standard Fade Out" (3s out, 0s in).
     It works for 90% of situations and sounds professional!

NOTE: Fades are applied BEFORE normalization. This ensures the
      fade curves remain smooth and natural-sounding.
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def track_gaps_help():
        return """
═══════════════════════════════════════════════════════════════════════
TRACK GAPS (PAUSES) EXPLAINED
═══════════════════════════════════════════════════════════════════════

Track gaps are the silent pauses between songs on an audio CD. The
standard is 2 seconds, but you can customize this for different effects.

WHAT ARE TRACK GAPS?
• Silent pauses inserted between tracks
• Standard Red Book CD specification: 2 seconds
• Gaps are separate from the audio tracks themselves
• CD players handle gaps automatically during playback

WHY CUSTOMIZE GAPS?
Different music styles benefit from different gap lengths:

STANDARD 2-SECOND GAPS:
✓ Normal albums and compilations
✓ Most commercial CDs use this
✓ Safe default for general use

NO GAPS (0 seconds) - GAPLESS PLAYBACK:
✓ DJ mixes and continuous sets
✓ Live concert recordings
✓ Progressive rock/electronic albums designed to flow
✓ Concept albums where tracks blend together
⚠ NOTE: Some older CD players may add a tiny pause anyway

SHORT GAPS (0.5-1 second):
✓ Live albums (maintains concert atmosphere)
✓ Fast-paced music (punk, metal)
✓ Keeping energy high between tracks

LONG GAPS (3-5 seconds):
✓ Classical music (separation between movements)
✓ Spoken word or podcasts
✓ Meditation/relaxation CDs
✓ When you want clear separation between tracks

CUSTOM INDIVIDUAL GAPS:
You can set different gap lengths for each track:
• Track 1-3: 2 seconds (normal songs)
• Track 4: 0 seconds (blends into track 5)
• Track 5-10: 2 seconds
• Track 11: 4 seconds (bonus track after pause)

TECHNICAL DETAILS:
• Gaps are implemented as "PREGAP" in the TOC file
• Pregap appears BEFORE the track it's assigned to
• Measured in CD frames (75 frames = 1 second)
• Gaps don't use disc capacity (they're silence)
• Actually, small gaps DO count toward capacity

HOW GAPS AFFECT PLAYBACK:
1. CD player reads track table at disc start
2. When track ends, player enters pause/gap
3. After gap time, next track begins
4. Skip/seek buttons navigate to track start (after gap)

COMPATIBILITY:
✓ All CD players support standard gaps
✓ Most players handle gapless correctly
✓ Custom gaps work on all Red Book compliant players
✓ Car stereos may add tiny pauses even with 0s gaps

PRESETS AVAILABLE:
1. Standard (2s all tracks) - Default, works everywhere
2. Live Album (0.5s gaps) - Maintains concert flow
3. DJ Mix (no gaps) - Seamless mixing
4. Classical (3s gaps) - Separation between movements

RECOMMENDATIONS:
• For most users: Use standard 2-second gaps
• For live recordings: Use 0.5s or gapless
• For DJ mixes: Use gapless (0s)
• For classical: Use 3-4 second gaps
• For special effects: Customize individual gaps

TIP: If unsure, stick with the 2-second default. It's the industry
     standard for a reason!

NOTE: Track gaps add to total disc time, so very long gaps on many
      tracks could affect capacity (though this is rarely an issue).
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def cdtext_help():
        return """
═══════════════════════════════════════════════════════════════════════
CD-TEXT SUPPORT EXPLAINED
═══════════════════════════════════════════════════════════════════════

CD-TEXT is a metadata format that embeds track and album information
directly into the audio CD. Compatible players can read and display
this information without needing an internet connection or database.

WHAT INFORMATION IS STORED:
✓ Album title and artist
✓ Individual track titles
✓ Track artists/performers
✓ Composer information (if available)
✓ Genre and year

WHERE CD-TEXT WORKS:
✓ Modern car stereos (most 2005+)
✓ Home CD players with LCD displays
✓ Computer CD/DVD drives with supporting software
✓ Portable CD players (newer models)

METADATA SOURCES:
The program automatically reads metadata from your audio files:
• MP3 files → ID3 tags
• FLAC files → Vorbis comments
• M4A/AAC → iTunes-style tags
• Others → file tags where available

EDITING METADATA:
You'll be given the option to:
• Review extracted metadata before burning
• Edit track titles, artists, and album info
• Bulk edit (set same artist for all tracks)
• Fix any incorrect or missing information

CD-TEXT LIMITATIONS:
• Maximum 80 characters per field
• ASCII text recommended (international characters may not display)
• Not all CD players support CD-TEXT (but won't cause problems)
• Older CD players ignore CD-TEXT data

COMPATIBILITY:
• CD-TEXT discs play in ALL CD players (including old ones)
• Players without CD-TEXT support simply ignore the extra data
• No quality or compatibility issues
• Standard Red Book audio CD format

TECHNICAL REQUIREMENTS:
• cdrdao with CD-TEXT support (most Linux distributions)
• ffprobe for reading audio file metadata
• The program handles all the technical details automatically

WHEN TO USE CD-TEXT:
✓ YES - For car audio (very useful!)
✓ YES - For mixtapes/compilations you'll use frequently
✓ YES - When you want professional-looking discs
✗ NO  - If burning for very old players that have issues
✗ NO  - When metadata extraction fails (program will warn you)

TIP: CD-TEXT makes your burned discs feel like store-bought albums!
     Your car stereo will show "Now Playing: Artist - Song Title"
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def preview_help():
        return """
═══════════════════════════════════════════════════════════════════════
TRACK PREVIEW EXPLAINED
═══════════════════════════════════════════════════════════════════════

Preview lets you hear your tracks before burning to verify they're
correct and in the right order.

WHY PREVIEW?
✓ Verify you selected the right files
✓ Check track order is correct
✓ Ensure audio quality is acceptable
✓ Catch corrupted or wrong files before wasting a CD

PREVIEW OPTIONS:
1. Quick Preview (10 seconds each)
   - Fast way to verify all tracks
   - Good for checking order and file validity

2. Extended Preview (30 seconds each)
   - More time to judge audio quality
   - Better for unfamiliar tracks

3. Preview Specific Track
   - Listen to one particular track
   - Useful for double-checking problem files

4. Preview Range
   - Preview tracks 5-10, for example
   - Useful for large collections

CONTROLS DURING PREVIEW:
• Press Ctrl+C to skip to the next track
• You'll be asked if you want to continue after each skip
• Audio plays through your system's default audio output

REQUIREMENTS:
• ffplay (part of ffmpeg package)
• Working audio output on your system
• Install with: sudo apt-get install ffmpeg

TIPS:
• Use headphones for better audio quality judgment
• Preview at least a few seconds to catch encoding issues
• If a track sounds wrong, cancel the burn and check the file
• Preview is especially useful for compilation CDs from mixed sources

NOTE: Preview plays the actual audio file, not the converted WAV.
      The burned CD will sound identical.
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def capacity_calculator_help():
        return """
═══════════════════════════════════════════════════════════════════════
DISC CAPACITY CALCULATOR EXPLAINED
═══════════════════════════════════════════════════════════════════════

The capacity calculator analyzes your audio files to ensure they fit
on a standard audio CD before burning.

CD SIZES:
• 74-minute CD = 4,440 seconds (older standard)
• 80-minute CD = 4,800 seconds (modern standard)

Most CDs sold today are 80-minute. Check your CD packaging to be sure.

WHAT IT DOES:
• Reads duration metadata from each audio file
• Calculates total playing time
• Shows remaining capacity
• Warns if tracks exceed disc capacity
• Displays visual progress bar
• Includes track gap time in calculations

AUDIO FILE METADATA:
The calculator uses ffprobe to read embedded duration information from:
✓ MP3 files (from frame headers)
✓ FLAC files (from stream info)
✓ M4A/AAC files (from container metadata)
✓ WAV files (from RIFF header)
✓ All other supported formats

IMPORTANT NOTES:
• Duration is based on PLAYBACK time, not file size
• A 10MB MP3 and 50MB FLAC of the same song take the same CD space
• Audio CDs use uncompressed PCM, so file size doesn't matter
• Variable bitrate (VBR) files are handled correctly
• Track gaps are included in the total time

WHAT IF FILES DON'T FIT?
1. Remove some tracks
2. Split into multiple CDs
3. Use CD-R 90 or 99-minute discs (rare, less compatible)
4. Re-encode files to slightly lower quality (not recommended)

OVERBURNING:
Some burners support "overburning" beyond 80 minutes, but:
✗ Not all CD players can read the overburned area
✗ Higher risk of burning errors
✗ May not work in car stereos or older players
✗ Not recommended for important/archival burns

TIP: Always leave a minute or two of buffer space for best
     compatibility and to account for any encoding variations.
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def playlist_help():
        return """
═══════════════════════════════════════════════════════════════════════
M3U/M3U8 PLAYLIST IMPORT EXPLAINED
═══════════════════════════════════════════════════════════════════════

M3U playlists are simple text files that list audio files in a specific
order. This feature preserves your carefully crafted playlist order when
burning to CD.

WHAT IS M3U?
- M3U = MPEG Audio Layer 3 URL (simple text format)
- M3U8 = UTF-8 encoded M3U (supports international characters)
- Just a list of file paths, one per line

EXAMPLE M3U FILE CONTENT:
#EXTM3U
#EXTINF:234,Artist - Song Title
/path/to/song1.mp3
#EXTINF:189,Another Artist - Another Song
/path/to/song2.mp3
./relative/path/song3.flac

PATH HANDLING:
• Absolute paths: /home/user/Music/song.mp3
• Relative paths: ./songs/track.mp3 (relative to playlist location)
• Windows paths: C:\Music\song.mp3 (converted automatically)

CREATING M3U PLAYLISTS:
Most music players can create playlists:
• VLC: Media → Save Playlist to File
• iTunes: File → Library → Export Playlist
• Windows Media Player: Right-click playlist → Save As
• Audacity: Can export track lists

ADVANTAGES:
✓ Preserves exact track order from your music player
✓ Mix songs from different folders
✓ Easy to edit in a text editor
✓ Portable between different music applications

PLAYLIST ORDER:
- Files are burned in the EXACT order they appear in the playlist
- No automatic reordering by track number or filename
- Perfect for mixtapes, DJ sets, or custom compilations

TIP: Create your perfect playlist in your favorite music player,
     export as M3U, then burn it directly to CD!
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def folder_scanning_help():
        return """
═══════════════════════════════════════════════════════════════════════
FOLDER SCANNING EXPLAINED
═══════════════════════════════════════════════════════════════════════

Instead of entering files one by one, you can point to a folder and
the program will automatically find all audio files.

RECURSIVE SCANNING:
- Scans the folder AND all subdirectories
- Useful for album folders with bonus tracks in subfolders
- Example folder structure:
  Album/
  ├── 01_song.mp3
  ├── 02_song.mp3
  └── Bonus/
      └── 03_bonus.mp3

NON-RECURSIVE SCANNING:
- Scans only the specified folder (no subdirectories)
- Faster and simpler for flat folder structures
- Use when all files are in one place

SUPPORTED FORMATS:
The scanner looks for these file types:
• MP3, WAV, FLAC, M4A/AAC
• OGG, WMA, OPUS

FILE ORDERING:
Files are sorted naturally by filename:
• "01_track.mp3" comes before "10_track.mp3" ✓
• Numbers in filenames are handled correctly
• You can still reorder using metadata track numbers

TIP: For best results, name your files with leading zeros:
     01_track.mp3, 02_track.mp3, ... 10_track.mp3
═══════════════════════════════════════════════════════════════════════"""
    
    @staticmethod
    def normalize_audio_help():
        return """
═══════════════════════════════════════════════════════════════════════
AUDIO NORMALIZATION EXPLAINED
═══════════════════════════════════════════════════════════════════════

Audio normalization adjusts the volume levels of your tracks to ensure
consistent playback volume across the entire CD.

WHY NORMALIZE?
- Different songs often have different volume levels
- Some tracks may be much quieter or louder than others
- Normalization prevents you from adjusting volume between tracks

WHAT IT DOES:
- Analyzes each track to find its peak volume
- Adjusts the gain so all tracks have similar perceived loudness
- Prevents audio clipping (distortion from too-high volume)

WHEN TO USE IT:
✓ YES - When burning tracks from different sources/albums
✓ YES - When some tracks sound noticeably quieter than others
✓ YES - For mixtapes or compilation CDs

WHEN NOT TO USE IT:
✗ NO - When burning a complete album that's already mastered
✗ NO - When preserving the original dynamic range is important
✗ NO - For classical music where volume variation is intentional

TECHNICAL NOTE:
This uses 'peak normalization' which adjusts based on the loudest
point in each track. Some audiophiles prefer 'loudness normalization'
(ReplayGain) which considers perceived loudness instead.

Normalization is NON-DESTRUCTIVE for this burn - your original files
remain unchanged.

ORDER OF OPERATIONS:
1. Fades are applied first (if requested)
2. Then normalization (if requested)
This ensures fade curves remain smooth and natural.
═══════════════════════════════════════════════════════════════════════"""

    @staticmethod
    def track_order_help():
        return """
═══════════════════════════════════════════════════════════════════════
TRACK ORDERING EXPLAINED
═══════════════════════════════════════════════════════════════════════

The program automatically determines track order using these methods:

1. METADATA TAGS (Highest Priority)
   • Reads embedded track numbers from MP3/FLAC/M4A files
   • Uses the 'track' field (e.g., "3" or "3/12")
   • Most reliable for albums ripped properly

2. FILENAME PATTERNS (Fallback)
   The program looks for these patterns:
   • "01_songname.mp3" or "01 - songname.mp3"
   • "Track 01.mp3" or "track_01.mp3"
   • Any number at the start of the filename

3. MANUAL OVERRIDE
   To force a specific order, rename your files:
   • 01_first_song.mp3
   • 02_second_song.mp3
   • 03_third_song.mp3

FILES WITHOUT NUMBERS:
- Placed at the end of the disc
- Sorted alphabetically

VERIFICATION:
- The program ALWAYS shows the proposed order before burning
- You can cancel and reorganize files if needed

TIP: Use a tool like 'EasyTAG' or 'Kid3' to edit metadata tags
     if your files don't have proper track numbers embedded.
═══════════════════════════════════════════════════════════════════════"""

    @staticmethod
    def burn_speed_help():
        return """
═══════════════════════════════════════════════════════════════════════
CD BURNING SPEED EXPLAINED
═══════════════════════════════════════════════════════════════════════

Burn speed affects both burn time and quality. Lower speeds generally
produce more reliable burns.

SPEED OPTIONS:
- 1x  = 80 minutes for full CD (highest quality, very slow)
- 2x  = 40 minutes for full CD (excellent quality, slow)
- 4x  = 20 minutes for full CD (very good quality, moderate)
- 8x  = 10 minutes for full CD (good quality, default)
- 16x = 5 minutes for full CD  (acceptable quality, fast)
- 24x+ = <5 minutes (lower quality, risk of errors)

RECOMMENDATIONS:
✓ Audio CDs: Use 4x-8x for best balance
✓ Master copies: Use 1x-4x for maximum quality
✓ Quick copies: 16x is usually fine
✓ Older CD players: Burn at 4x or lower

FACTORS TO CONSIDER:
- Older/cheaper CD players read slow burns better
- Car stereos often struggle with fast-burned discs
- High speeds can cause 'jitter' (timing errors)
- Your CD media has a maximum rated speed (check packaging)

TROUBLESHOOTING:
If CDs skip or won't play:
1. Try burning at a slower speed (4x or 2x)
2. Use better quality CD-R media
3. Clean your CD burner's lens

Current setting: 8x (recommended default)
═══════════════════════════════════════════════════════════════════════"""

    @staticmethod
    def cd_media_help():
        return """
═══════════════════════════════════════════════════════════════════════
CD MEDIA TYPES EXPLAINED
═══════════════════════════════════════════════════════════════════════

CD-R (Compact Disc-Recordable)
- Write once, permanent recording
- Compatible with almost all CD players
- Best for music CDs and final copies
- Cannot be erased or rewritten
- Capacity: 700MB / 80 minutes of audio

CD-RW (Compact Disc-ReWritable)
- Can be erased and rewritten (typically 1000+ times)
- More expensive than CD-R
- NOT compatible with some older CD players
- Good for temporary backups or testing
- Same capacity as CD-R

AUDIO CD CONSIDERATIONS:
- Use CD-R for maximum compatibility
- "Audio CD-R" or "Music CD-R" are marketing terms (same as regular CD-R)
- 74-minute vs 80-minute discs (80-min is standard now)

QUALITY BRANDS (Recommended):
- Verbatim (AZO dye)
- Taiyo Yuden (if you can find them)
- Sony
- Maxell

STORAGE TIPS:
- Store burned CDs in cases, away from sunlight
- Write on CDs with felt-tip markers only (not ballpoint)
- CD-Rs can last 50-200 years if stored properly
- Cheap media may degrade in 2-10 years

SPEED RATINGS:
Media is rated for maximum burn speed (e.g., 52x)
- It's safe to burn slower than the rating
- Never burn faster than the media rating
═══════════════════════════════════════════════════════════════════════"""
    @staticmethod
    def multi_session_help():
        return """
═══════════════════════════════════════════════════════════════════════
MULTI-SESSION SUPPORT EXPLAINED
═══════════════════════════════════════════════════════════════════════

Multi-session burning allows you to add tracks to a CD that hasn't been
finalized, making it possible to use a CD across multiple burn sessions.

WHAT IS MULTI-SESSION?
When you burn a CD without finalizing it, the disc remains "open" and
can accept additional tracks in future sessions. Each burn session adds
more tracks until you finalize the disc.

HOW IT WORKS:
1. First burn: Add tracks 1-5, leave disc OPEN
2. Second burn: Add tracks 6-10 to the same disc, leave OPEN
3. Third burn: Add tracks 11-15, FINALIZE the disc
Result: One CD with 15 tracks from 3 different burn sessions

FINALIZE vs KEEP OPEN:
- FINALIZE: Closes the disc, no more tracks can be added
  - Required for maximum compatibility with CD players
  - Disc is complete and ready to use anywhere

- KEEP OPEN: Leaves disc appendable for future sessions
  - Can add more tracks later
  - Some older CD players may not read open discs
  - Modern drives handle open discs fine

DISC COMPATIBILITY:
✓ CD-R: Supports multi-session (most common)
✓ CD-RW: Supports multi-session (can also erase and start over)
✗ Some older CD players: May only read finalized discs
✗ Car stereos: Often require finalized discs

USE CASES:
- Building a compilation over time
- Adding tracks as you acquire them
- Testing tracks before finalizing
- Filling a CD to exact capacity across multiple burns

IMPORTANT NOTES:
- Each session adds a ~14MB "lead-in/lead-out" overhead
- Too many sessions can waste disc space
- Always finalize before giving CD to someone
- Check disc status before adding tracks

TIP: If you're unsure whether you'll add more tracks, keep the disc
     open. You can always finalize later without adding tracks.
═══════════════════════════════════════════════════════════════════════"""

    @staticmethod
    def format_export_help():
        return """
═══════════════════════════════════════════════════════════════════════
MULTIPLE FORMAT EXPORT EXPLAINED
═══════════════════════════════════════════════════════════════════════

The format export feature allows you to convert your audio files into
multiple formats simultaneously, perfect for creating backups, sharing
music across different devices, or archiving your collection.

SUPPORTED FORMATS:

MP3 (MPEG-1 Audio Layer 3)
- Most universally compatible format
- Lossy compression (smaller files, some quality loss)
- Works on virtually all devices and players
- Quality: 128kbps (low), 192kbps (medium), 320kbps (high)
- Best for: Portability, compatibility, streaming

FLAC (Free Lossless Audio Codec)
- Lossless compression (perfect quality, larger files)
- No quality loss compared to original
- Open source and royalty-free
- Supports full metadata and album art
- Best for: Archival, audiophiles, home listening

OGG Vorbis
- Open source lossy compression
- Better quality than MP3 at same bitrate
- Not as widely supported as MP3
- Quality levels: 3 (low), 5 (medium), 7 (high), 10 (max)
- Best for: Open source enthusiasts, good quality/size ratio

AAC/M4A (Advanced Audio Coding)
- Used by Apple/iTunes
- Better quality than MP3 at same bitrate
- Native format for iPod, iPhone, iPad
- Quality: 128kbps (low), 192kbps (medium), 256kbps (high)
- Best for: Apple devices, iTunes users

Opus
- Modern, highly efficient codec
- Excellent quality at low bitrates
- Great for streaming and voice
- Quality: 96kbps (low), 128kbps (medium), 192kbps (high)
- Best for: Modern devices, web streaming

WAV (Waveform Audio File Format)
- Uncompressed PCM audio
- CD-quality (44.1kHz, 16-bit stereo)
- Large file sizes
- Universal compatibility
- Best for: CD burning, professional audio work

QUALITY SETTINGS:

LOW - Smaller files, acceptable quality
  MP3: 128kbps | AAC: 128kbps | OGG: Q3 | Opus: 96kbps
  Use for: Podcasts, audiobooks, low-storage devices

MEDIUM - Balanced quality and size
  MP3: 192kbps | AAC: 192kbps | OGG: Q5 | Opus: 128kbps
  Use for: General listening, most music

HIGH - Excellent quality, larger files
  MP3: 320kbps | AAC: 256kbps | OGG: Q7 | Opus: 192kbps
  Use for: Critical listening, archival copies

MAXIMUM/LOSSLESS - Best possible quality
  MP3: V0 VBR | AAC: 320kbps | OGG: Q10 | FLAC/WAV: Lossless
  Use for: Archival, audiophile listening, mastering

BATCH EXPORT WORKFLOW:
1. Select source files (manual, folder scan, or playlist)
2. Choose output directory
3. Select target formats (one or multiple)
4. Choose quality level
5. Confirm and export

The tool will convert all selected files to all chosen formats,
preserving metadata (tags, album art) when possible.

FILE SIZES (approximate for 4-minute song):
- WAV (uncompressed): ~40MB
- FLAC (lossless): ~25MB
- MP3 320kbps: ~10MB
- MP3 192kbps: ~6MB
- AAC 256kbps: ~8MB
- OGG Q7: ~7MB
- Opus 192kbps: ~6MB

TIPS:
- For archival: Use FLAC (lossless quality, compressed)
- For compatibility: Use MP3 at 320kbps
- For Apple devices: Use AAC/M4A
- For modern web: Use Opus
- Export multiple formats for different use cases

METADATA PRESERVATION:
All formats support metadata (artist, title, album, etc.)
Album art is preserved in: MP3, FLAC, AAC/M4A, OGG
═══════════════════════════════════════════════════════════════════════"""

    @staticmethod
    def album_art_help():
        return """
═══════════════════════════════════════════════════════════════════════
ALBUM ART EMBEDDING EXPLAINED
═══════════════════════════════════════════════════════════════════════

Album art (also called cover art) is an image embedded directly into
audio files, allowing music players to display artwork while playing.

WHAT IS ALBUM ART?

Album art is a digital image (usually JPEG or PNG) that's stored inside
the audio file itself, not as a separate file. Modern music players,
smartphones, and car stereos display this artwork automatically.

SUPPORTED FORMATS:

✓ MP3 (ID3v2 tags)
  - Most common format
  - Supports front cover, back cover, and more
  - Maximum recommended size: 1200x1200 pixels

✓ M4A/MP4/AAC (iTunes format)
  - Native iTunes/Apple format
  - Excellent compatibility with iOS devices
  - High-quality image support

✓ FLAC (Lossless)
  - Supports multiple pictures
  - Front cover, back cover, artist, etc.
  - Perfect for archival quality

✓ OGG Vorbis (Open source)
  - Supports embedded pictures
  - Good compatibility

✗ WAV (Not supported)
  - WAV files don't support embedded metadata
  - Use separate image files or convert to FLAC

IMAGE RECOMMENDATIONS:

Size:
- Minimum: 300x300 pixels (acceptable)
- Recommended: 600x600 pixels (good quality)
- High quality: 1000x1000 or 1200x1200 pixels
- Maximum: 1400x1400 (larger = bigger file size)

Format:
- JPEG: Best for photos, smaller files
- PNG: Best for graphics/text, supports transparency
- Square aspect ratio (1:1) is standard

File Size:
- Keep under 500KB for best compatibility
- 100-200KB is ideal for most uses
- Very large images can bloat file sizes

FEATURES:

1. EMBED ALBUM ART
   - Add artwork to single or multiple files
   - Batch embed same art to entire album
   - Automatically handles format conversion

2. EXTRACT ALBUM ART
   - Save embedded artwork as separate image file
   - Useful for sharing or editing
   - Auto-detects image format

3. CHECK ALBUM ART
   - See if files have embedded artwork
   - View image dimensions and format
   - Batch check multiple files

4. REMOVE ALBUM ART
   - Strip artwork from files
   - Reduce file size
   - Clean metadata

5. BATCH OPERATIONS
   - Embed same art into entire folder
   - Process albums efficiently
   - Recursive folder scanning

WORKFLOW EXAMPLES:

Adding art to a new album:
1. Download or scan album cover (square, 1000x1000)
2. Use "Batch embed from folder"
3. Select album folder and cover image
4. All tracks get the same artwork

Extracting art from a file:
1. Select "Extract album art"
2. Choose source audio file
3. Image is saved as FILENAME_cover.jpg

Cleaning up files:
1. Use "Check album art" to see status
2. Use "Remove album art" if needed
3. Re-embed with better quality image

WHERE ALBUM ART APPEARS:

✓ Music players (iTunes, Windows Media Player, VLC)
✓ Smartphones (iPhone Music, Android players)
✓ Car stereos (most modern units)
✓ Portable music players (iPod, etc.)
✓ Smart speakers (Alexa, Google Home with displays)
✓ Desktop/taskbar notifications
✓ Lock screen displays

FILE SIZE IMPACT:

Adding a 200KB album art image to 100 MP3 files:
- Original album: 500MB
- With album art: 520MB (4% increase)
- Minimal impact for huge visual benefit!

COMMON ISSUES:

Problem: Album art doesn't show in player
Solution: Some players cache artwork. Restart player or
          rebuild music library.

Problem: Image looks pixelated
Solution: Use higher resolution source image (1000x1000+)

Problem: Files too large after embedding
Solution: Use JPEG instead of PNG, or compress image first

Problem: Wrong image shows up
Solution: Remove old art first, then embed new art

TIPS:

- Use consistent image sizes across your library (e.g., all 1000x1000)
- Square images (1:1 ratio) work best
- Download artwork from iTunes, Amazon, or Discogs
- For classical music, use composer/conductor photos
- Compilations often use generic artwork or logos
- Consider different art for different editions (Deluxe, Remaster)

SOURCES FOR ALBUM ART:

- iTunes Store (right-click album → Copy artwork)
- Amazon Music (high-res artwork available)
- Discogs.com (community-sourced artwork)
- MusicBrainz (free, community database)
- Album artist's official website
- Your own scans of physical CDs

BEST PRACTICES:

1. Always keep original high-res artwork files
2. Embed after all other editing is complete
3. Use same artwork across all tracks in an album
4. Test playback on target devices
5. Backup files before batch operations
═══════════════════════════════════════════════════════════════════════"""

    @staticmethod
    def batch_burn_help():
        return """
═══════════════════════════════════════════════════════════════════════
BATCH BURN QUEUE EXPLAINED
═══════════════════════════════════════════════════════════════════════

The Batch Burn Queue allows you to prepare multiple CDs and burn them
sequentially without manual intervention between each disc.

WHAT IS BATCH BURNING?

Batch burning lets you:
- Queue up multiple CD projects
- Set individual settings for each CD
- Burn them one after another
- Only swap discs between burns
- Track success/failure for each job

IDEAL FOR:
✓ Burning multiple albums from a collection
✓ Creating multiple copies of the same disc
✓ Making compilation CDs for different people
✓ Archiving music collections to CD
✓ Producing multiple discs for distribution

HOW IT WORKS:

1. BUILD THE QUEUE
   - Add multiple "jobs" (CDs) to the queue
   - Each job has:
     • Name (e.g., "Beatles - Abbey Road")
     • Audio files to burn
     • Individual settings (speed, normalization, etc.)

2. CONFIGURE EACH JOB
   - Files: Manual entry, folder scan, or playlist
   - Settings: Use defaults or customize per CD
   - Name: Give each CD a descriptive name

3. START BATCH PROCESS
   - Review queue
   - Start sequential burning
   - Insert disc when prompted
   - Process continues automatically

4. TRACK PROGRESS
   - Real-time status for each job
   - Completion times tracked
   - Failed jobs logged with reasons
   - Final summary report

QUEUE MANAGEMENT:

Add CD to Queue:
- Enter name and select files
- Configure settings or use defaults
- Job added to end of queue

Remove CD from Queue:
- Select job by number
- Removes without affecting others

View Queue Details:
- See all jobs and their settings
- Check file lists
- Review configurations

Clear Queue:
- Remove all jobs at once
- Start fresh

JOB SETTINGS:

Each job can have individual settings:
- Burn speed (1-52x)
- Audio normalization (on/off)
- CD-TEXT metadata (on/off)
- Track gaps (custom or default)
- Fade effects (custom or none)
- Multi-session mode
- Finalization

Quick Setup:
Choose "Use default settings" for fast queue building:
- Normalize: ON
- Speed: 8x
- CD-TEXT: ON
- Standard gaps
- No fades
- Finalize disc

BATCH BURN PROCESS:

1. Review queue summary
2. Confirm start
3. For each job:
   a. Display job name and settings
   b. Prompt to insert blank disc
   c. Check disc status
   d. Burn CD with configured settings
   e. Mark as completed/failed
   f. Continue to next job
4. Display final summary

STATUS INDICATORS:

⏸ Pending   - Not yet burned
✓ Completed - Successfully burned
✗ Failed    - Burn error occurred
⊘ Skipped   - User skipped this job

ERROR HANDLING:

If a job fails:
- Error is logged with details
- You're asked: Continue or abort?
- Other jobs remain in queue
- Can retry failed jobs later

Common skip reasons:
- No disc inserted
- Disc not blank
- User cancelled

TIME ESTIMATION:

Approximate burn times per CD:
- 8x speed: 10-12 minutes for full CD
- 16x speed: 6-8 minutes
- 4x speed: 18-20 minutes

Total batch time = (burn time + disc swap time) × number of CDs

Example: 5 CDs at 8x speed ≈ 60 minutes total

BEST PRACTICES:

1. PREPARATION
   - Have all blank CDs ready beforehand
   - Use same media type for consistency
   - Test one CD before queuing many
   - Verify file accessibility

2. ORGANIZATION
   - Use descriptive job names
   - Group similar albums together
   - Keep backup of queue configuration
   - Note any special requirements

3. SETTINGS
   - Lower speeds (4-8x) for better quality
   - Enable normalization for consistent volume
   - Use CD-TEXT for player compatibility
   - Test settings on single CD first

4. MONITORING
   - Stay nearby during batch process
   - Check first few burns for quality
   - Have extra blank CDs available
   - Note which jobs fail for retry

5. QUALITY CONTROL
   - Verify first burned CD
   - Spot-check others randomly
   - Keep failed discs for analysis
   - Document any recurring issues

WORKFLOW EXAMPLES:

Example 1: Burning 3 Albums
1. Add "Album 1" with folder scan
2. Add "Album 2" with playlist
3. Add "Album 3" manual file entry
4. Start batch burn
5. Insert disc when prompted for each
6. All 3 CDs burned automatically

Example 2: Multiple Copies
1. Add "Mix CD - Copy 1" with files
2. Add "Mix CD - Copy 2" (same files)
3. Add "Mix CD - Copy 3" (same files)
4. All use identical settings
5. Burn 3 identical CDs sequentially

Example 3: Different Settings
1. Add "Classical Album" - slow speed, no normalization
2. Add "Rock Album" - fast speed, normalize on
3. Add "Audiobook" - medium speed, no CD-TEXT
4. Each burns with custom settings

TROUBLESHOOTING:

Problem: Job fails repeatedly
Solution: Check media quality, reduce burn speed,
          verify files are not corrupted

Problem: Disc swap takes too long
Solution: Have next disc ready, keep discs organized,
          use CD spindle for easy access

Problem: Forgot to add a CD
Solution: Add new jobs anytime before starting batch,
          or add after batch completes and run again

Problem: Need to stop batch
Solution: Choose "no" when asked to continue after error,
          remaining jobs stay pending for later

ADVANTAGED FEATURES:

Queue Persistence:
- Queue is active during program session
- Cleared when exiting to main menu
- Rebuild queue if needed

Mixed Media:
- Can queue both 74-min and 80-min CDs
- Each job checks capacity independently
- Different settings per job

Flexibility:
- Pause between jobs (don't insert disc)
- Skip problematic jobs
- Continue from where you left off
- Retry failed jobs by rebuilding queue

RECORD KEEPING:

After batch burn completes:
- Final summary shows all results
- Note failed jobs for investigation
- Track average burn times
- Plan future batches accordingly

TIPS FOR EFFICIENCY:

- Prepare all files beforehand
- Have blank CDs stacked in order
- Use consistent naming scheme
- Group similar content together
- Test one before queueing many
- Use default settings for speed
- Keep workspace organized
- Document successful configurations

LIMITATIONS:

- Cannot edit job after adding (remove and re-add)
- No automatic disc ejection prompts
- Must be present to swap discs
- No pause/resume within a job
- Queue cleared when returning to main menu

With batch burning, you can efficiently create multiple CDs with
minimal manual intervention, perfect for archiving collections or
producing multiple copies!
═══════════════════════════════════════════════════════════════════════"""

def main():
    """Enhanced main program with audio CD support, CD-TEXT, track gaps, fades, verification, help system, and folder scanning."""
    writer = AudioCDWriter()
    organizer = MusicCDOrganizer()
    help_sys = HelpSystem()
    
    # Check for required tools
    required_tools = ['wodim', 'ffmpeg']
    optional_tools = ['cdparanoia', 'cdrdao', 'sox']
    
    print("Checking for required tools...")
    for tool in required_tools:
        result = subprocess.run(['which', tool], capture_output=True)
        if result.returncode != 0:
            print(f"✗ {tool} not found. Install with: sudo apt-get install {tool}")
            sys.exit(1)
    
    for tool in optional_tools:
        result = subprocess.run(['which', tool], capture_output=True)
        if result.returncode != 0:
            print(f"⚠ {tool} not found (optional). Install for full features: sudo apt-get install {tool}")
    
    while True:
        print("\nSinge 1.1.3")
        print("1. Burn audio CD (with automatic track ordering)")
        print("2. Burn audio CD from folder")
        print("3. Burn audio CD from M3U/M3U8 playlist")
        print("4. Add tracks to existing CD (multi-session)")
        print("5. Check disc status")
        print("6. Rip audio CD (preserves track order)")
        print("7. Verify last burned CD")
        print("8. Create CUE sheet")
        print("9. Export to multiple formats")
        print("10. Album art manager")
        print("11. Batch burn queue")
        print("12. Help topics")
        print("13. Exit")

        choice = input("\nSelect option (1-13): ").strip()
        
        if choice in ['1', '2', '3']:
            # Common workflow for all audio CD burning options
            
            # Step 1: Get files based on choice
            if choice == '1':
                files = []
                print("\nEnter audio file paths (MP3, WAV, FLAC, etc.)")
                print("Files will be automatically sorted by track number")
                print("Enter one file per line, empty line to finish:")
                
                while True:
                    file_path = input().strip()
                    if not file_path:
                        break
                    files.append(file_path)
                
                if not files:
                    continue
                
                # Organize files by track number from metadata
                organized_files = organizer.organize_by_track_number(files)
            
            elif choice == '2':
                folder_path = input("\nEnter folder path: ").strip()
                
                if not folder_path:
                    continue
                
                # Ask if recursive scanning is desired
                while True:
                    recursive_response = input("Scan subdirectories too? (y/n/?): ").strip().lower()
                    if recursive_response == '?':
                        print(help_sys.folder_scanning_help())
                    elif recursive_response in ['y', 'n']:
                        recursive = (recursive_response == 'y')
                        break
                    else:
                        print("Please enter 'y' for yes, 'n' for no, or '?' for help")
                
                # Scan folder for audio files
                files = writer.scan_folder_for_audio(folder_path, recursive)
                
                if not files:
                    continue
                
                # Organize files by track number from metadata
                organized_files = organizer.organize_by_track_number(files)
            
            elif choice == '3':
                playlist_path = input("\nEnter M3U/M3U8 playlist path: ").strip()
                
                if not playlist_path:
                    continue
                
                # Parse playlist
                organized_files = writer.parse_m3u_playlist(playlist_path)
                
                if not organized_files:
                    continue
                
                print("\n" + "="*70)
                print("NOTE: Playlist order will be preserved exactly as listed.")
                print("Tracks will NOT be reordered by metadata or filename.")
                print("="*70)
            
            # Step 2: Configure track gaps
            track_gaps = writer.configure_track_gaps(len(organized_files))
            
            # Display gap preview
            track_names = [os.path.basename(f) for f in organized_files]
            writer.display_gap_preview(track_names, track_gaps)
            
            # Step 3: Configure fade effects
            print("\n" + "="*70)
            print("Next: Configure fade in/out effects for tracks")
            print("="*70)
            fade_ins, fade_outs = writer.configure_fades(len(organized_files), track_names)
            
            # Display fade preview
            writer.display_fade_preview(track_names, fade_ins, fade_outs)
            
            # Step 4: Calculate and display capacity (including gaps)
            cd_size = 80  # Default to 80-minute CD
            capacity_info = writer.calculate_disc_capacity(organized_files, cd_size, track_gaps)
            writer.display_capacity_summary(capacity_info)
            
            if not capacity_info['fits_on_disc']:
                print("\n✗ Cannot proceed - files exceed disc capacity")
                continue
            
            # Step 5: Offer track preview (for folder and playlist modes)
            if choice in ['2', '3']:
                while True:
                    preview_response = input("\nPreview tracks before burning? (y/n/?): ").strip().lower()
                    if preview_response == '?':
                        print(help_sys.preview_help())
                    elif preview_response == 'y':
                        writer.interactive_preview_menu(organized_files)
                        break
                    elif preview_response == 'n':
                        break
                    else:
                        print("Please enter 'y' for yes, 'n' for no, or '?' for help")
            
            # Step 6: Confirm track order, gaps, and fades
            while True:
                response = input("\nProceed with this configuration? (y/n/?): ").strip().lower()
                if response == '?':
                    print(help_sys.track_order_help())
                elif response == 'y':
                    break
                elif response == 'n':
                    print("Cancelled.")
                    break
                else:
                    print("Please enter 'y' for yes, 'n' for no, or '?' for help")
            
            if response != 'y':
                continue
            
            # Step 7: Ask about CD-TEXT
            use_cdtext = writer.ask_yes_no_with_help(
                "Enable CD-TEXT (embed track names/artist info)?",
                help_sys.cdtext_help()
            )
            
            # Step 8: Ask about normalization
            normalize = writer.ask_yes_no_with_help(
                "Normalize audio levels?",
                help_sys.normalize_audio_help()
            )
            
            # Step 9: Ask about burn speed
            while True:
                speed_response = input("Burn speed (4/8/16/?): ").strip()
                if speed_response == '?':
                    print(help_sys.burn_speed_help())
                elif speed_response in ['4', '8', '16']:
                    burn_speed = int(speed_response)
                    break
                else:
                    burn_speed = 8  # Default
                    break
            
            # Step 10: Burn!
            print("\n" + "="*70)
            print("READY TO BURN")
            print("="*70)
            print("\nPlease insert a blank CD-R disc into the drive.")
            input("Press Enter when ready to start burning...")
            
            if writer.burn_audio_cd(organized_files, normalize, burn_speed, 
                                   use_cdtext=use_cdtext, track_gaps=track_gaps,
                                   fade_ins=fade_ins, fade_outs=fade_outs):
                print("\n" + "="*70)
                print("✓✓✓ AUDIO CD BURNED SUCCESSFULLY! ✓✓✓")
                print("="*70)
                print("\nYour CD includes:")
                if use_cdtext:
                    print("  ✓ CD-TEXT metadata (track/artist info)")
                print(f"  ✓ Custom track gaps")
                fade_count = sum(1 for f_in, f_out in zip(fade_ins, fade_outs) if f_in > 0 or f_out > 0)
                if fade_count > 0:
                    print(f"  ✓ Fade effects on {fade_count} track(s)")
                if normalize:
                    print("  ✓ Normalized audio levels")
                
                # Step 11: Offer verification
                print("\n" + "="*70)
                print("VERIFICATION RECOMMENDED")
                print("="*70)
                print("Verify the burned CD to ensure data integrity.")
                
                verify_method = writer.choose_verification_method()
                
                if verify_method:
                    print("\nPlease keep the CD in the drive for verification...")
                    input("Press Enter when ready to verify...")
                    
                    # Perform verification
                    verification_passed = writer.verify_burned_disc(
                        writer.last_burn_wav_files,
                        writer.last_burn_checksums,
                        verify_method
                    )
                    
                    if verification_passed:
                        print("\n🎉 SUCCESS! Your CD is perfect and ready to use!")
                    else:
                        print("\n⚠ Verification failed. Consider re-burning at a slower speed")
                        print("   or using different media.")
                else:
                    print("\n✓ Skipping verification.")
                    print("  Your CD should be ready, but verification is recommended.")
                
                print("\nEnjoy your professionally mastered audio CD!")
            else:
                print("\n✗ Failed to burn audio CD")
        
        elif choice == '4':
            # Multi-session: Add tracks to existing CD
            print("\n" + "="*70)
            print("MULTI-SESSION MODE: Add Tracks to Existing CD")
            print("="*70)
            
            # Check disc status first
            disc_info = writer.check_disc_status()
            writer.display_disc_status(disc_info)
            
            if not disc_info['inserted']:
                print("\nPlease insert a disc and try again.")
                continue
            
            if disc_info['finalized']:
                print("\n✗ This disc is finalized and cannot accept more tracks.")
                print("  Use a CD-RW and erase it, or use a different disc.")
                continue
            
            if disc_info['blank']:
                print("\n⚠ This is a blank disc. Use regular burn mode (option 1, 2, or 3) instead.")
                response = input("Continue with multi-session mode anyway? (y/n): ").strip().lower()
                if response != 'y':
                    continue
            
            # Get files to add
            files = []
            print("\nEnter audio file paths to ADD to the disc")
            print("Enter one file per line, empty line to finish:")
            
            while True:
                file_path = input().strip()
                if not file_path:
                    break
                files.append(file_path)
            
            if not files:
                continue
            
            # Organize files by track number from metadata
            organized_files = organizer.organize_by_track_number(files)
            
            # Configure track gaps
            track_gaps = writer.configure_track_gaps(len(organized_files))
            
            # Display gap preview
            track_names = [os.path.basename(f) for f in organized_files]
            writer.display_gap_preview(track_names, track_gaps)
            
            # Configure fade effects
            print("\n" + "="*70)
            print("Configure fade in/out effects for tracks")
            print("="*70)
            fade_ins, fade_outs = writer.configure_fades(len(organized_files), track_names)
            
            # Display fade preview
            writer.display_fade_preview(track_names, fade_ins, fade_outs)
            
            # Calculate and display capacity
            cd_size = 80
            capacity_info = writer.calculate_disc_capacity(organized_files, cd_size, track_gaps)
            writer.display_capacity_summary(capacity_info)
            
            if not capacity_info['fits_on_disc']:
                print("\n✗ Cannot proceed - files exceed disc capacity")
                continue
            
            # Offer track preview
            while True:
                preview_response = input("\nPreview tracks before burning? (y/n/?): ").strip().lower()
                if preview_response == '?':
                    print(help_sys.preview_help())
                elif preview_response == 'y':
                    writer.interactive_preview_menu(organized_files)
                    break
                elif preview_response == 'n':
                    break
                else:
                    print("Please enter 'y' for yes, 'n' for no, or '?' for help")
            
            # Ask about track order
            while True:
                response = input("\nProceed with this configuration? (y/n/?): ").strip().lower()
                if response == '?':
                    print(help_sys.track_order_help())
                elif response == 'y':
                    break
                elif response == 'n':
                    print("Cancelled.")
                    break
                else:
                    print("Please enter 'y' for yes, 'n' for no, or '?' for help")
            
            if response != 'y':
                continue
            
            # Ask about CD-TEXT
            use_cdtext = writer.ask_yes_no_with_help(
                "Enable CD-TEXT (embed track names/artist info)?",
                help_sys.cdtext_help()
            )
            
            # Ask about normalization
            normalize = writer.ask_yes_no_with_help(
                "Normalize audio levels?",
                help_sys.normalize_audio_help()
            )
            
            # Ask if should finalize
            finalize = writer.ask_yes_no_with_help(
                "Finalize disc after adding tracks? (no = keep open for more sessions)",
                help_sys.multi_session_help()
            )
            
            # Ask about burn speed
            while True:
                speed_response = input("Burn speed (4/8/16/?): ").strip()
                if speed_response == '?':
                    print(help_sys.burn_speed_help())
                elif speed_response in ['4', '8', '16']:
                    burn_speed = int(speed_response)
                    break
                else:
                    burn_speed = 8
                    break
            
            print("\n" + "="*70)
            print("READY TO ADD TRACKS (MULTI-SESSION)")
            print("="*70)
            print("\nThe disc should already be in the drive.")
            input("Press Enter when ready to start burning...")
            
            if writer.burn_audio_cd(organized_files, normalize, burn_speed,
                                use_cdtext=use_cdtext, track_gaps=track_gaps,
                                fade_ins=fade_ins, fade_outs=fade_outs,
                                multi_session=True, finalize=finalize):
                print("\n" + "="*70)
                print("✓✓✓ TRACKS ADDED SUCCESSFULLY! ✓✓✓")
                print("="*70)
                if finalize:
                    print("\n  Disc has been finalized.")
                    print("  It is now complete and compatible with all CD players.")
                else:
                    print("\n  Disc remains OPEN - you can add more tracks later.")
                    print("  Remember to finalize it before giving to others!")
            else:
                print("\n✗ Failed to add tracks")
        
        elif choice == '5':
            # Check disc status
            disc_info = writer.check_disc_status()
            writer.display_disc_status(disc_info)
        
        elif choice == '6':
            output_dir = input("Enter output directory (default: ./ripped_tracks): ").strip()
            if not output_dir:
                output_dir = "./ripped_tracks"
            
            ripped = writer.rip_audio_cd(output_dir)
            if ripped:
                print(f"✓ Ripped {len(ripped)} tracks to {output_dir}")
            else:
                print("✗ Failed to rip CD")
        
        elif choice == '7':
            # Verify last burned CD
            if not writer.last_burn_wav_files or not writer.last_burn_checksums:
                print("\n✗ No burn data available for verification.")
                print("  Verification data is only available immediately after burning.")
                print("  Please burn a CD first, then verify it.")
                continue
            
            print("\n" + "="*70)
            print("VERIFY LAST BURNED CD")
            print("="*70)
            print(f"\nVerification data available for {len(writer.last_burn_wav_files)} track(s)")
            print("Please insert the CD you just burned into the drive.")
            input("Press Enter when ready to verify...")
            
            verify_method = writer.choose_verification_method()
            
            if verify_method:
                verification_passed = writer.verify_burned_disc(
                    writer.last_burn_wav_files,
                    writer.last_burn_checksums,
                    verify_method
                )
                
                if verification_passed:
                    print("\n🎉 SUCCESS! Your CD is perfect!")
                else:
                    print("\n⚠ Verification failed.")
            else:
                print("Verification cancelled.")
        
        elif choice == '8':
            files = []
            print("\nEnter audio file paths for CUE sheet:")
            while True:
                file_path = input().strip()
                if not file_path:
                    break
                files.append(file_path)
            
            if files:
                organized_files = organizer.organize_by_track_number(files)
                writer.create_cue_sheet(organized_files)
        
        elif choice == '9':
            # Export to multiple formats
            print("\n" + "="*70)
            print("EXPORT TO MULTIPLE FORMATS")
            print("="*70)
            print("\nHow would you like to select files?")
            print("1. Enter file paths manually")
            print("2. Scan a folder")
            print("3. Use M3U/M3U8 playlist")
            
            source_choice = input("\nSelect option (1-3): ").strip()
            
            export_files = []
            
            if source_choice == '1':
                print("\nEnter audio file paths (one per line, empty line to finish):")
                while True:
                    file_path = input().strip()
                    if not file_path:
                        break
                    export_files.append(file_path)
            
            elif source_choice == '2':
                folder_path = input("\nEnter folder path: ").strip()
                if folder_path:
                    recursive = input("Scan subdirectories? (y/n): ").strip().lower() == 'y'
                    export_files = writer.scan_folder_for_audio(folder_path, recursive)
            
            elif source_choice == '3':
                playlist_path = input("\nEnter M3U/M3U8 playlist path: ").strip()
                if playlist_path:
                    export_files = writer.parse_m3u_playlist(playlist_path)
            
            if export_files:
                writer.export_formats_interactive(export_files)
            else:
                print("\n✗ No files selected for export")
        
        elif choice == '10':
            # Album art manager
            writer.album_art_manager_interactive()
        
        elif choice == '11':
            # Batch burn queue
            batch_queue = BatchBurnQueue()
            
            while True:
                batch_queue.display_queue()
                
                print("\nBatch Burn Queue Options:")
                print("1. Add CD to queue")
                print("2. Remove CD from queue")
                print("3. View queue details")
                print("4. Start batch burn")
                print("5. Clear queue")
                print("6. Back to main menu")
                
                batch_choice = input("\nSelect option (1-6): ").strip()
                
                if batch_choice == '1':
                    # Add CD to queue
                    print("\n" + "-"*70)
                    print("ADD CD TO BATCH QUEUE")
                    print("-"*70)
                    
                    job_name = input("\nEnter name for this CD (e.g., 'Album 1', 'Rock Mix'): ").strip()
                    if not job_name:
                        job_name = f"CD {len(batch_queue.jobs) + 1}"
                    
                    print("\nHow to add files:")
                    print("1. Enter file paths manually")
                    print("2. Scan a folder")
                    print("3. Use M3U/M3U8 playlist")
                    
                    source_choice = input("\nSelect option (1-3): ").strip()
                    
                    audio_files = []
                    
                    if source_choice == '1':
                        print("\nEnter audio file paths (one per line, empty line to finish):")
                        while True:
                            file_path = input().strip()
                            if not file_path:
                                break
                            audio_files.append(file_path)
                    
                    elif source_choice == '2':
                        folder_path = input("\nEnter folder path: ").strip()
                        if folder_path:
                            recursive = input("Scan subdirectories? (y/n): ").strip().lower() == 'y'
                            audio_files = writer.scan_folder_for_audio(folder_path, recursive)
                    
                    elif source_choice == '3':
                        playlist_path = input("\nEnter M3U/M3U8 playlist path: ").strip()
                        if playlist_path:
                            audio_files = writer.parse_m3u_playlist(playlist_path)
                    
                    if not audio_files:
                        print("\n✗ No files selected. Job not added.")
                        continue
                    
                    # Configure settings
                    print(f"\n{len(audio_files)} file(s) selected.")
                    print("\nConfigure burn settings:")
                    
                    use_defaults = input("Use default settings? (y/n): ").strip().lower() == 'y'
                    
                    if use_defaults:
                        settings = {
                            'normalize': True,
                            'speed': 8,
                            'use_cdtext': True,
                            'track_gaps': None,
                            'fade_ins': None,
                            'fade_outs': None,
                            'multi_session': False,
                            'finalize': True
                        }
                    else:
                        normalize = input("Normalize audio? (y/n): ").strip().lower() == 'y'
                        speed = int(input("Burn speed (1-52, recommended 8): ").strip() or "8")
                        use_cdtext = input("Use CD-TEXT? (y/n): ").strip().lower() == 'y'
                        
                        settings = {
                            'normalize': normalize,
                            'speed': speed,
                            'use_cdtext': use_cdtext,
                            'track_gaps': None,  # Use defaults
                            'fade_ins': None,
                            'fade_outs': None,
                            'multi_session': False,
                            'finalize': True
                        }
                    
                    # Create and add job
                    job = BurnJob(job_name, audio_files, settings)
                    batch_queue.add_job(job)
                    
                    print(f"\n✓ '{job_name}' added to queue")
                
                elif batch_choice == '2':
                    # Remove CD from queue
                    if not batch_queue.jobs:
                        print("\n✗ Queue is empty")
                        continue
                    
                    batch_queue.display_queue()
                    try:
                        index = int(input("\nEnter job number to remove: ").strip()) - 1
                        job = batch_queue.get_job(index)
                        if job:
                            if batch_queue.remove_job(index):
                                print(f"\n✓ '{job.name}' removed from queue")
                        else:
                            print("\n✗ Invalid job number")
                    except ValueError:
                        print("\n✗ Invalid input")
                
                elif batch_choice == '3':
                    # View queue details
                    batch_queue.display_queue()
                    
                    if batch_queue.jobs:
                        print("\nDetailed information:")
                        for i, job in enumerate(batch_queue.jobs, 1):
                            print(f"\n{i}. {job.name}")
                            print(f"   Status: {job.status}")
                            print(f"   Tracks: {len(job.audio_files)}")
                            print(f"   Files: {', '.join(Path(f).name for f in job.audio_files[:3])}..."
                                  if len(job.audio_files) > 3
                                  else f"   Files: {', '.join(Path(f).name for f in job.audio_files)}")
                            print(f"   Settings: Speed={job.settings.get('speed')}x, "
                                  f"Normalize={job.settings.get('normalize')}, "
                                  f"CD-TEXT={job.settings.get('use_cdtext')}")
                    
                    input("\nPress Enter to continue...")
                
                elif batch_choice == '4':
                    # Start batch burn
                    writer.batch_burn_interactive(batch_queue)
                
                elif batch_choice == '5':
                    # Clear queue
                    if batch_queue.jobs:
                        confirm = input(f"\nClear all {len(batch_queue.jobs)} job(s)? (y/n): ").strip().lower()
                        if confirm == 'y':
                            batch_queue.jobs.clear()
                            batch_queue.current_job_index = 0
                            print("\n✓ Queue cleared")
                    else:
                        print("\n✗ Queue is already empty")
                
                elif batch_choice == '6':
                    # Back to main menu
                    break
                
                else:
                    print("Invalid option")
        
        elif choice == '12':
            print("\n=== HELP TOPICS ===")
            print("1. Multi-Session Support (NEW!)")
            print("2. CD Verification")
            print("3. Fade In/Out Effects")
            print("4. Track Gaps/Pauses")
            print("5. CD-TEXT Support")
            print("6. Folder Scanning")
            print("7. M3U Playlist Import")
            print("8. Track Preview")
            print("9. Audio Normalization")
            print("10. Track Ordering")
            print("11. Burn Speed")
            print("12. CD Media Types")
            print("13. Format Export")
            print("14. Album Art")
            print("15. Batch Burn Queue (NEW!)")
            print("16. Back to main menu")

            help_choice = input("\nSelect help topic (1-16): ").strip()
            
            if help_choice == '1':
                print(help_sys.multi_session_help())
            if help_choice == '2':
                print(help_sys.verification_help())
            if help_choice == '3':
                print(help_sys.fade_effects_help())
            if help_choice == '4':
                print(help_sys.track_gaps_help())
            if help_choice == '5':
                print(help_sys.cdtext_help())
            if help_choice == '6':
                print(help_sys.folder_scanning_help())
            if help_choice == '7':
                print(help_sys.playlist_help())
            if help_choice == '8':
                print(help_sys.preview_help())
            if help_choice == '9':  
                print(help_sys.normalize_audio_help())
            if help_choice == '10':
                print(help_sys.track_order_help())
            if help_choice == '11':
                print(help_sys.burn_speed_help())
            if help_choice == '12':
                print(help_sys.cd_media_help())
            if help_choice == '13':
                print(help_sys.format_export_help())
            if help_choice == '14':
                print(help_sys.album_art_help())
            if help_choice == '15':
                print(help_sys.batch_burn_help())
            if help_choice == '16':
                continue
        
        elif choice == '13':
            print("\n" + "="*70)
            print("Thank you for using Singe.")
            print("Goodbye!")
            print("="*70)
            break

if __name__ == "__main__":
    main()