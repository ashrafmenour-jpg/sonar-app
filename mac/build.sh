#!/bin/bash
# builds Sonar.app (universal: Apple silicon + Intel) — runs on GitHub's macOS builder
set -e
RUN="${GITHUB_RUN_NUMBER:-1}"; OUT=build; APP="$OUT/Sonar.app"
rm -rf "$OUT"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/www"
swiftc -O -target arm64-apple-macos12 mac/Sonar.swift -o "$OUT/sonar-arm64"
swiftc -O -target x86_64-apple-macos12 mac/Sonar.swift -o "$OUT/sonar-x86"
lipo -create "$OUT/sonar-arm64" "$OUT/sonar-x86" -output "$APP/Contents/MacOS/Sonar"
sed "s/__RUN__/$RUN/g" mac/Info.plist > "$APP/Contents/Info.plist"
IS="$OUT/Sonar.iconset"; mkdir -p "$IS"
for s in 16 32 128 256 512; do sips -z $s $s mac/icon.png --out "$IS/icon_${s}x${s}.png" >/dev/null; d=$((s*2)); sips -z $d $d mac/icon.png --out "$IS/icon_${s}x${s}@2x.png" >/dev/null; done
iconutil -c icns "$IS" -o "$APP/Contents/Resources/Sonar.icns"
cp web/index.html web/version.json "$APP/Contents/Resources/www/"
codesign --force --deep -s - "$APP"
ditto -c -k --keepParent "$APP" Sonar-Mac.zip
ls -la Sonar-Mac.zip
