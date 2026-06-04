# Steganography Techniques

## metadata extraction
**When:** Any image or audio file; flag often hidden in EXIF, XMP, or ID3 tags.
**Tools:** `exiftool -a -u`, `exiv2`, `mediainfo`
**Caveats:** Check all tag groups including GPS, thumbnail, MakerNote.

## LSB image steganography
**When:** PNG/BMP with suspicious noise in pixel values; file size larger than expected.
**Tools:** `zsteg` (PNG/BMP), `steghide` (JPEG/BMP/WAV), custom Python PIL extraction.
**Caveats:** May be bit-order or channel-order variant (RGB vs BGR, bit 0 vs bit 7).

## PNG chunk inspection
**When:** PNG has unusual file size; might embed data in tEXt, iTXt, zTXt, or custom chunks.
**Tools:** `pngcheck -v`, `python -c "import png; ..."`, `binwalk`
**Caveats:** Hidden IDAT chunks after IEND are valid for some tools but ignored by browsers.

## audio spectrogram
**When:** WAV or MP3 with no obvious audible clue; flag visible in frequency domain.
**Tools:** Audacity (spectrogram view), `sox`, `python matplotlib.specgram()`
**Caveats:** Try both linear and log frequency scale; look for text in 0–20kHz range.

## whitespace / unicode steganography
**When:** Text file; flag may be in trailing spaces (SNOW), zero-width Unicode chars.
**Tools:** `cat -A` (trailing spaces), `xxd | grep 'e2 80'` (zero-width chars).
**Caveats:** Zero-width joiners/non-joiners invisible in most editors.

## file carving / binwalk
**When:** Image or binary contains embedded files (zip inside PNG, etc.).
**Tools:** `binwalk -e`, `foremost`, `7z x`
**Caveats:** Carved files may be incomplete; check offsets with `binwalk` hex output.
