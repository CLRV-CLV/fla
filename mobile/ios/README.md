# FLA iOS build and personal signing

`build_ipa.sh` cross-compiles an **unsigned** arm64 iOS device IPA on Linux. It pins Zig to 0.13.0 because newer Zig releases currently fail to link this Apple SDK target, downloads the Theos iPhoneOS SDK if needed, and runs `validate_ipa.py` after packaging.

```bash
bash mobile/ios/build_ipa.sh
python3 mobile/ios/validate_ipa.py desktop/bin/FLA-ios.ipa
```

The app and ReplayKit upload extension are intentionally compatible with personal/free provisioning as far as their entitlements are concerned:

- The previous generated entitlements file hard-coded `application-identifier` without a Team ID and requested `group.top.clrv.fla`. A personal profile usually does not grant that App Group; passing those overrides to a signing tool can produce a bundle that installs but is rejected at launch.
- The new bundle does **not** ship a custom entitlements plist. Let the signing tool derive the app identifier and entitlements from the selected provisioning profile; do not add an App Group unless that exact group is enabled in the profile.
- App Group storage is optional at runtime. When the profile grants `group.top.clrv.fla`, the extension can use it (including for LAN sessions); when it does not, the extension retrieves a public-relay session from FLA's server using the device vendor ID. A LAN-only pairing cannot be handed to the extension without an authorized App Group, so personal/free-profile installs should use a public relay for screen casting. The extension's broadcast mode is declared under `NSExtensionAttributes`.

The build/validator checks the IPA ZIP, both arm64 device Mach-O executables, bundle IDs, executable permissions, and ReplayKit extension metadata. It cannot perform Apple's code signing or verify an installation on a real iPhone. The IPA must still be signed with a valid personal profile before installation; an actual device crash log is required to conclusively diagnose any remaining device-only failure.
