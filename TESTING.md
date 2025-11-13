# Testing Documentation for Singe

This document describes how to run the test suite for the Singe CD burner application.

## Test Suite

The test suite is located in `test_singe.py` and provides comprehensive coverage of all main classes in the Singe application.

## Running Tests

### Run all tests:
```bash
python3 test_singe.py
```

### Run tests with verbose output (already default):
```bash
python3 test_singe.py -v
```

### Run specific test class using unittest:
```bash
python3 -m unittest test_singe.TestProgressBar
python3 -m unittest test_singe.TestBurnJob
python3 -m unittest test_singe.TestBatchBurnQueue
python3 -m unittest test_singe.TestAudioCDWriter
python3 -m unittest test_singe.TestMusicCDOrganizer
python3 -m unittest test_singe.TestHelpSystem
```

### Run specific test method:
```bash
python3 -m unittest test_singe.TestProgressBar.test_init
```

## Test Coverage

The test suite includes **46 unit tests** covering:

### ProgressBar (6 tests)
- Initialization with custom parameters
- Update with auto-increment
- Update with explicit current value
- Update with new suffix
- Time formatting (seconds to MM:SS)
- Finish method

### BurnJob (5 tests)
- Initialization with name, files, and settings
- Summary generation for pending status
- Summary generation for completed status
- Summary generation for failed status
- Summary generation for skipped status

### BatchBurnQueue (8 tests)
- Initialization
- Adding jobs to queue
- Removing jobs from queue
- Getting job by index
- Getting next pending job
- Summary for empty queue
- Summary with various job statuses

### AudioCDWriter (10 tests)
- Initialization and device detection
- Audio file extension constants
- CD capacity constants (74-min and 80-min)
- Default gap and fade constants
- Disc status checking (no disc)
- Disc status checking (blank disc)
- MD5 checksum calculation
- SHA1 checksum calculation
- SHA256 checksum calculation
- Checksum for nonexistent file

### MusicCDOrganizer (2 tests)
- Initialization with AudioCDWriter
- Organizing files by track number metadata

### HelpSystem (15 tests)
- All help text methods:
  - Multi-session help
  - Verification help
  - Fade effects help
  - Track gaps help
  - CD-Text help
  - Folder scanning help
  - Playlist help
  - Preview help
  - Normalize audio help
  - Track order help
  - Burn speed help
  - CD media help
  - Format export help
  - Album art help
  - Batch burn help

## Test Results

All tests use mocking to avoid requiring actual CD burning hardware or external dependencies like `wodim`, `cdrdao`, or `ffmpeg`.

**Latest Results: 46/46 tests passing ✅**

## Requirements

The tests require:
- Python 3.12+ (tested with Python 3.12.3)
- Standard library modules only (no external dependencies)

## Test Structure

Tests are organized using Python's `unittest` framework with the following features:
- Mock objects for external command execution
- Temporary files for checksum testing
- Comprehensive assertions for expected behavior
- Detailed test documentation

## Continuous Integration

These tests can be easily integrated into a CI/CD pipeline:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: python3 test_singe.py
```

## Contributing

When adding new features to Singe:
1. Add corresponding tests to `test_singe.py`
2. Run the full test suite to ensure no regressions
3. Aim for high code coverage of new functionality
