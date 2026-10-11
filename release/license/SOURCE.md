Microsoft Visual C++ v14 app-local x64 CRT

Version: 14.51.36247.0. These are all 12 x64 DLLs in the official installer’s
vcRuntimeMinimum_amd64/cab1.cab payload. MFC Additional Runtime DLLs are a separate payload and are not included. Windows 10/11
provide the Universal CRT as an operating-system component.

Official download: https://aka.ms/vc14/vc_redist.x64.exe
Official license: https://aka.ms/VCRedistLicense
Official license document:
https://visualstudio.microsoft.com/wp-content/uploads/2025/10/Visual-C-V14-License-Redistributable_and_Runtime_ENU.docx
Redistribution documentation:
https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files

The installer was downloaded from Microsoft. No third-party DLL source was
used. No Windows installer was run and no system runtime was changed.
The CAB payload hash was verified against the Microsoft Burn bundle manifest.
Every DLL was checked as PE AMD64 (0x8664), and its embedded file version was
read. Full hashes and source metadata are in manifest.json. Authenticode trust
verification still needs Windows; the original installer is retained in cache.

Extraction can be repeated on macOS:
python3 windows-release/build_tools/extract_msvc_runtime.py

The accompanying license document is Microsoft’s original file. The TXT file
contains text extracted from that document. The download’s runtime license
does not itself grant redistribution rights; redistribution is subject to the
applicable Visual Studio license terms and Microsoft’s redistributable list.
