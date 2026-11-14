# Gradle gerektirmeden APK üretir: javac -> d8 -> aapt2 -> zipalign -> apksigner
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
$bt = "$sdk\build-tools\35.0.1"
$jar = "$sdk\platforms\android-35\android.jar"
$jdk = "C:\jdk-23.0.2"
$env:JAVA_HOME = $jdk; $env:PATH = "$jdk\bin;$env:PATH"
$out = "$here\build"; $dist = "$here\dist"
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$out\classes", "$out\gen", "$out\dex", $dist | Out-Null

python "$here\gen_icons.py"
& "$bt\aapt2.exe" compile --dir "$here\res" -o "$out\res.zip"; if ($LASTEXITCODE) { throw "aapt2 compile" }
& "$bt\aapt2.exe" link -o "$out\base.apk" -I $jar --manifest "$here\AndroidManifest.xml" --java "$out\gen" `
    --min-sdk-version 26 --target-sdk-version 34 --version-code 1 --version-name 1.0 "$out\res.zip"; if ($LASTEXITCODE) { throw "aapt2 link" }

$src = @(Get-ChildItem "$here\src" -Recurse -Filter *.java | ForEach-Object FullName) + @(Get-ChildItem "$out\gen" -Recurse -Filter *.java | ForEach-Object FullName)
& "$jdk\bin\javac.exe" -source 8 -target 8 -Xlint:-options -cp $jar -d "$out\classes" $src; if ($LASTEXITCODE) { throw "javac" }
$classes = Get-ChildItem "$out\classes" -Recurse -Filter *.class | ForEach-Object FullName
& "$bt\d8.bat" --release --min-api 26 --lib $jar --output "$out\dex" $classes; if ($LASTEXITCODE) { throw "d8" }

python -c "import zipfile,shutil;shutil.copy(r'$out\base.apk',r'$out\unsigned.apk');z=zipfile.ZipFile(r'$out\unsigned.apk','a',zipfile.ZIP_DEFLATED);z.write(r'$out\dex\classes.dex','classes.dex');z.close()"
& "$bt\zipalign.exe" -f -p 4 "$out\unsigned.apk" "$out\aligned.apk"; if ($LASTEXITCODE) { throw "zipalign" }

# İmza anahtarı (bir kez üretilir; güncellemelerin yüklenebilmesi için SAKLAYIN, paylaşmayın)
$ks = "$here\keystore\release.jks"; $pwf = "$here\keystore\pass.txt"
if (-not (Test-Path $ks)) {
    New-Item -ItemType Directory -Force "$here\keystore" | Out-Null
    $pw = -join ((48..57) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    Set-Content $pwf $pw -NoNewline
    & "$jdk\bin\keytool.exe" -genkeypair -keystore $ks -storepass $pw -keypass $pw -alias canvas -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Canvas Asistanim" | Out-Null
}
$pw = Get-Content $pwf -Raw
& "$bt\apksigner.bat" sign --ks $ks --ks-pass "pass:$pw" --key-pass "pass:$pw" --out "$dist\CanvasAsistanim.apk" "$out\aligned.apk"; if ($LASTEXITCODE) { throw "apksigner" }
& "$bt\apksigner.bat" verify "$dist\CanvasAsistanim.apk"
Get-Item "$dist\CanvasAsistanim.apk" | Select-Object FullName, @{n = 'KB'; e = { [math]::Round($_.Length / 1KB) } }
