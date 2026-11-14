# Canvas'a güvenli bağlanma: token ekranda görünmez, dosyaya yazılmaz, sadece bu terminal oturumunda kalır.
$url = Read-Host "Canvas adresiniz (örn. https://okulunuz.instructure.com)"
$sec = Read-Host "Canvas token (yazarken görünmez)" -AsSecureString
$env:CANVAS_BASE_URL = $url.Trim().TrimEnd('/')
$env:CANVAS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

Write-Host "`n--- 1) Erisim kontrolu ---"
python -m canvas_agent check
if ($LASTEXITCODE -ne 0) { Write-Host "Baglanti basarisiz; adresi/token'i kontrol edin."; return }

Write-Host "`n--- 2) Ilk senkronizasyon (mevcut durumu kaydeder, bildirim gondermez) ---"
python -m canvas_agent sync

Write-Host "`nHazir. Surekli izleme icin:  python -m canvas_agent run"
Write-Host "Arayuz icin:                 python -m canvas_agent serve"
