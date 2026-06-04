# Forensics Techniques

## PCAP analysis
**When:** Network capture provided; flag may be in HTTP body, DNS query, or TCP stream.
**Tools:** `tshark -r cap.pcap -Y http`, `wireshark`, `tcpflow`
**Caveats:** Follow TCP streams for multi-packet payloads; check for TLS (needs key).

## strings / grep on binary
**When:** Memory dump, disk image, or unknown binary; flag may be in raw strings.
**Tools:** `strings -n 8`, `grep -a 'CTF{' dump.bin`
**Caveats:** Wide-char (UTF-16) strings need `strings -e l`; check both endiannesses.

## disk image mounting
**When:** FAT32/ext4/NTFS image; deleted or hidden files may contain flag.
**Tools:** `mount -o loop`, `autopsy`, `sleuthkit (fls, icat)`
**Caveats:** Check unallocated space and file slack with `icat`.

## memory dump analysis
**When:** Volatility-compatible dump (WinXP, Win7, Linux); flag in process memory or registry.
**Tools:** `volatility -f mem.dmp imageinfo`, `pslist`, `dumpfiles`, `filescan`
**Caveats:** Pick correct profile; hibernation files need `imagecopy` first.

## log analysis / timeline
**When:** Access logs, auth logs, or syslog; flag derivable from sequence of events.
**Tools:** `grep`, `awk`, `cut`, `sort | uniq -c`; import to Splunk/ELK for large sets.
**Caveats:** Check timezone offsets; timestamps may be UTC vs local.

## archive forensics
**When:** ZIP/tar with password or hidden entries; extra data appended after EOF.
**Tools:** `zipdetails`, `7z l -slt`, `binwalk`, `zip2john`
**Caveats:** ZIP comment field can hold data; stored-not-compressed entries bypass crypto.
