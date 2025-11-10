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

class AudioCDWriter:
    # Supported audio file extensions
    AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.opus'}
    
    # CD capacity constants
    CD_74_MIN_SECONDS = 74 * 60  # 4440 seconds
    CD_80_MIN_SECONDS = 80 * 60  # 4800 seconds
    
    def __init__(self):
        self.device = self._detect_cd_device()
        
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
                # -autoexit: exit when done
                # -nodisp: no video display window
                # -t: duration to play
                # -loglevel quiet: suppress ffplay output
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
    
    def calculate_disc_capacity(self, audio_files: List[str], cd_size: int = 80) -> Dict:
        """
        Calculate total duration and remaining capacity for a list of audio files.
        
        Args:
            audio_files: List of audio file paths
            cd_size: CD size in minutes (74 or 80)
            
        Returns:
            Dictionary with capacity information
        """
        total_seconds = 0
        track_durations = []
        failed_files = []
        
        print("\nCalculating disc capacity...")
        
        for i, audio_file in enumerate(audio_files, 1):
            duration = self.get_audio_duration(audio_file)
            
            if duration is not None:
                total_seconds += duration
                track_durations.append({
                    'file': audio_file,
                    'duration': duration
                })
                # Show progress
                print(f"  Track {i}/{len(audio_files)}: {Path(audio_file).name} - {self._format_time(duration)}")
            else:
                failed_files.append(audio_file)
                print(f"  Track {i}/{len(audio_files)}: {Path(audio_file).name} - [Duration unknown]")
        
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
            'fits_on_disc': remaining_seconds >= 0
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
        
        for track in tracks:
            track_num = track['number']
            output_file = os.path.join(output_dir, f"track_{track_num:02d}.wav")
            
            print(f"Ripping Track {track_num}...")
            
            # Use cdparanoia to rip the track
            result = subprocess.run(
                ['cdparanoia', '-w', str(track_num), output_file],
                capture_output=True
            )
            
            if result.returncode == 0:
                ripped_files.append(output_file)
                print(f"✓ Track {track_num} ripped successfully")
            else:
                print(f"✗ Failed to rip track {track_num}")
        
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
    
    def burn_audio_cd(self, audio_files: List[str], normalize: bool = True, speed: int = 8, dry_run: bool = False) -> bool:
        """Burn audio files to CD in the specified order."""
        
        if dry_run:
            print("\n" + "="*70)
            print("DRY RUN MODE - NO ACTUAL BURNING WILL OCCUR")
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
        
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_files = []
            
            # Convert all files to WAV format
            print("\n" + ("="*70 if not dry_run else ""))
            print("STEP 1: Converting files to WAV format" + (" (simulated)" if dry_run else ""))
            print("="*70)
            
            for i, audio_file in enumerate(audio_files_sorted, 1):
                if not os.path.exists(audio_file):
                    print(f"Warning: File not found: {audio_file}")
                    continue
                
                # Check if already WAV
                if audio_file.lower().endswith('.wav'):
                    if dry_run:
                        print(f"✓ Track {i}: {os.path.basename(audio_file)} - Already WAV, no conversion needed")
                    wav_files.append(audio_file)
                else:
                    # Convert to WAV
                    wav_output = os.path.join(temp_dir, f"track_{i:02d}.wav")
                    print(f"{'[DRY RUN] ' if dry_run else ''}Converting {os.path.basename(audio_file)} to WAV...")
                    
                    if not dry_run:
                        if self.convert_to_wav(audio_file, wav_output):
                            wav_files.append(wav_output)
                        else:
                            print(f"Failed to convert {audio_file}")
                    else:
                        # In dry run, simulate successful conversion
                        wav_files.append(wav_output)
                        print(f"  ✓ Would convert: {audio_file} → {wav_output}")
            
            if not wav_files:
                print("No valid audio files to burn")
                return False
            
            # Normalize audio if requested
            if normalize:
                print("\n" + "="*70)
                print("STEP 2: Normalizing audio levels" + (" (simulated)" if dry_run else ""))
                print("="*70)
                
                normalized_files = []
                
                for i, wav_file in enumerate(wav_files, 1):
                    norm_output = os.path.join(temp_dir, f"norm_track_{i:02d}.wav")
                    
                    if dry_run:
                        print(f"[DRY RUN] Would normalize track {i}: {os.path.basename(wav_file)}")
                        normalized_files.append(wav_file)
                    else:
                        # Use sox for normalization
                        result = subprocess.run(
                            ['sox', wav_file, norm_output, 'norm'],
                            capture_output=True
                        )
                        
                        if result.returncode == 0:
                            normalized_files.append(norm_output)
                        else:
                            normalized_files.append(wav_file)  # Use original if norm fails
                
                wav_files = normalized_files
            else:
                print("\n" + "="*70)
                print("STEP 2: Skipping normalization (not requested)")
                print("="*70)
            
            # Create TOC file for cdrdao (more control over track order)
            print("\n" + "="*70)
            print("STEP 3: Creating TOC file" + (" (simulated)" if dry_run else ""))
            print("="*70)
            
            toc_file = os.path.join(temp_dir, "audio.toc")
            
            if not dry_run:
                with open(toc_file, 'w') as f:
                    f.write("CD_DA\n\n")
                    
                    for i, wav_file in enumerate(wav_files, 1):
                        f.write(f"// Track {i}\n")
                        f.write("TRACK AUDIO\n")
                        f.write(f'FILE "{wav_file}" 0\n\n')
                print(f"✓ TOC file created: {toc_file}")
            else:
                print("[DRY RUN] TOC file contents that would be created:")
                print("─"*70)
                print("CD_DA\n")
                for i, wav_file in enumerate(wav_files, 1):
                    print(f"// Track {i}")
                    print("TRACK AUDIO")
                    print(f'FILE "{os.path.basename(wav_file)}" 0\n')
                print("─"*70)
            
            # Burn using cdrdao for precise track control
            print("\n" + "="*70)
            print(f"STEP 4: Burning to CD at {speed}x speed" + (" (simulated)" if dry_run else ""))
            print("="*70)
            
            if dry_run:
                print(f"\n[DRY RUN] Command that would be executed:")
                print(f"  cdrdao write --device {self.device} --speed {speed} --eject {toc_file}")
                print(f"\nAlternative command (if cdrdao fails):")
                print(f"  wodim dev={self.device} -v -audio -pad speed={speed} -eject [wav files...]")
                print("\n✓ DRY RUN COMPLETE - No CD was burned")
                print("="*70)
                return True
            
            burn_cmd = [
                'cdrdao', 'write',
                '--device', self.device,
                '--speed', str(speed),
                '--eject',
                toc_file
            ]
            
            result = subprocess.run(burn_cmd)
            
            if result.returncode != 0:
                # Fallback to wodim if cdrdao fails
                print("Trying alternative burn method with wodim...")
                burn_cmd = [
                    'wodim',
                    f'dev={self.device}',
                    '-v',
                    '-audio',
                    '-pad',
                    f'speed={speed}',
                    '-eject'
                ] + wav_files
                
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
                
                return {
                    'title': tags.get('title', 'Unknown'),
                    'artist': tags.get('artist', 'Unknown'),
                    'album': tags.get('album', 'Unknown'),
                    'track': tags.get('track', '0'),
                    'duration': data.get('format', {}).get('duration', '0')
                }
        except:
            pass
        
        return {'title': 'Unknown', 'artist': 'Unknown', 
                'album': 'Unknown', 'track': '0'}
    
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


def main():
    """Enhanced main program with audio CD support, help system, and folder scanning."""
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
        print("\nSinge 1.0.7")
        print("1. Burn audio CD (with automatic track ordering)")
        print("2. Burn audio CD from folder")
        print("3. Burn audio CD from M3U/M3U8 playlist")
        print("4. Rip audio CD (preserves track order)")
        print("5. Burn data CD")
        print("6. Create CUE sheet")
        print("7. Help topics")
        print("8. Exit")
        
        choice = input("\nSelect option (1-8): ").strip()
        
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
            
            if files:
                # Organize files by track number from metadata
                organized_files = organizer.organize_by_track_number(files)
                
                # Calculate and display capacity
                cd_size = 80  # Default to 80-minute CD
                capacity_info = writer.calculate_disc_capacity(organized_files, cd_size)
                writer.display_capacity_summary(capacity_info)
                
                if not capacity_info['fits_on_disc']:
                    print("\n✗ Cannot proceed - files exceed disc capacity")
                    continue
                
                # Ask about track order with help option
                while True:
                    response = input("\nProceed with this track order? (y/n/?): ").strip().lower()
                    if response == '?':
                        print(help_sys.track_order_help())
                    elif response == 'y':
                        break
                    elif response == 'n':
                        print("Cancelled. You may want to rename your files or edit their metadata.")
                        break
                    else:
                        print("Please enter 'y' for yes, 'n' for no, or '?' for help")
                
                if response == 'y':
                    # Ask about normalization with help option
                    normalize = writer.ask_yes_no_with_help(
                        "Normalize audio levels?",
                        help_sys.normalize_audio_help()
                    )
                    
                    # Ask about burn speed with help option
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
                    
                    if writer.burn_audio_cd(organized_files, normalize, burn_speed):
                        print("✓ Audio CD burned successfully!")
                    else:
                        print("✗ Failed to burn audio CD")
        
        elif choice == '2':
            folder_path = input("\nEnter folder path: ").strip()
            
            if folder_path:
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
                
                if files:
                    # Organize files by track number from metadata
                    organized_files = organizer.organize_by_track_number(files)
                    
                    # Calculate and display capacity
                    cd_size = 80  # Default to 80-minute CD
                    capacity_info = writer.calculate_disc_capacity(organized_files, cd_size)
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
                    
                    # Ask about track order with help option
                    while True:
                        response = input("\nProceed with this track order? (y/n/?): ").strip().lower()
                        if response == '?':
                            print(help_sys.track_order_help())
                        elif response == 'y':
                            break
                        elif response == 'n':
                            print("Cancelled. You may want to rename your files or edit their metadata.")
                            break
                        else:
                            print("Please enter 'y' for yes, 'n' for no, or '?' for help")
                    
                    if response == 'y':
                        # Ask about normalization with help option
                        normalize = writer.ask_yes_no_with_help(
                            "Normalize audio levels?",
                            help_sys.normalize_audio_help()
                        )
                        
                        # Ask about burn speed with help option
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
                        
                        if writer.burn_audio_cd(organized_files, normalize, burn_speed):
                            print("✓ Audio CD burned successfully!")
                        else:
                            print("✗ Failed to burn audio CD")
        
        elif choice == '3':
            playlist_path = input("\nEnter M3U/M3U8 playlist path: ").strip()
            
            if playlist_path:
                # Parse playlist
                files = writer.parse_m3u_playlist(playlist_path)
                
                if files:
                    print("\n" + "="*70)
                    print("NOTE: Playlist order will be preserved exactly as listed.")
                    print("Tracks will NOT be reordered by metadata or filename.")
                    print("="*70)
                    
                    # Calculate and display capacity
                    cd_size = 80  # Default to 80-minute CD
                    capacity_info = writer.calculate_disc_capacity(files, cd_size)
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
                            writer.interactive_preview_menu(files)
                            break
                        elif preview_response == 'n':
                            break
                        else:
                            print("Please enter 'y' for yes, 'n' for no, or '?' for help")
                    
                    # Ask if user wants to proceed with playlist order
                    while True:
                        response = input("\nBurn tracks in playlist order? (y/n/?): ").strip().lower()
                        if response == '?':
                            print(help_sys.playlist_help())
                        elif response == 'y':
                            break
                        elif response == 'n':
                            print("Cancelled. Edit your playlist file to change track order.")
                            break
                        else:
                            print("Please enter 'y' for yes, 'n' for no, or '?' for help")
                    
                    if response == 'y':
                        # Ask about normalization with help option
                        normalize = writer.ask_yes_no_with_help(
                            "Normalize audio levels?",
                            help_sys.normalize_audio_help()
                        )
                        
                        # Ask about burn speed with help option
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
                        
                        # Burn with playlist order (no reordering)
                        # We'll pass files directly without organizing by metadata
                        if writer.burn_audio_cd(files, normalize, burn_speed):
                            print("✓ Audio CD burned successfully!")
                        else:
                            print("✗ Failed to burn audio CD")
        
        elif choice == '4':
            output_dir = input("Enter output directory (default: ./ripped_tracks): ").strip()
            if not output_dir:
                output_dir = "./ripped_tracks"
            
            ripped = writer.rip_audio_cd(output_dir)
            if ripped:
                print(f"✓ Ripped {len(ripped)} tracks to {output_dir}")
            else:
                print("✗ Failed to rip CD")
        
        elif choice == '4':
            print("Use the original cd_burner.py for data CDs")
        
        elif choice == '5':
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
        
        elif choice == '6':
            print("\n=== HELP TOPICS ===")
            print("1. Folder Scanning")
            print("2. Audio Normalization")
            print("3. Track Ordering")
            print("4. Burn Speed")
            print("5. CD Media Types")
            print("6. Back to main menu")
            
            help_choice = input("\nSelect help topic (1-6): ").strip()
            
            if help_choice == '1':
                print(help_sys.folder_scanning_help())
            elif help_choice == '2':
                print(help_sys.normalize_audio_help())
            elif help_choice == '3':
                print(help_sys.track_order_help())
            elif help_choice == '4':
                print(help_sys.burn_speed_help())
            elif help_choice == '5':
                print(help_sys.cd_media_help())
        
        elif choice == '7':
            print("Goodbye!")
            break


if __name__ == "__main__":
    main()