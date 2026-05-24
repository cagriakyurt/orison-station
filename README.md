# ORISON // Taktik Radyo Terminali & FM RDS İstasyonu

ORISON, Raspberry Pi'nin donanımsal DMA (Direct Memory Access) motorunu kullanarak GPIO pinleri üzerinden doğrudan FM yayını ve dinamik RDS (Radio Data System) verisi iletmesini sağlayan, mobil öncelikli (PWA) modern bir web kontrol terminalidir.

Bu proje, **PiFmRds** çekirdeğini taban alarak üzerine gelişmiş ses sentezleme (text-to-speech), Mors kodu üreteci, dijital bant geçiren ses filtre efektleri ve parazit simülasyonları ekler.

---

## 📡 Temel Özellikler

- **Dinamik Frekans Ayarı (MHz):** `87.5 - 108.0 MHz` aralığında arayüzdeki slider ile frekansın anlık olarak güncellenmesi.
- **Dinamik RDS Kontrolü:** Yayın esnasında istasyon adı (PS - Program Service, max 8 karakter) ve detay metninin (RT - Radio Text, max 64 karakter) dinamik olarak güncellenebilmesi.
- **Yayın Önizleme Modu (Preview):** Herhangi bir yayını FM üzerinden havaya vermeden önce tarayıcı üzerinden anlık olarak dinleme olanağı.
- **Taktiksel Ses Filtreleri:**
  - *AM Telsiz Modu:* Standart askeri telsiz bandı simülasyonu (bandpass filter).
  - *Sığınak Yankısı:* Reverb efektli yeraltı sığınağı ses simülasyonu.
  - *Bant Sürüklenmesi:* Tremolo efektli eski bant kaydı hissi.
  - *Ham Net Ses:* Filtre uygulanmamış temiz ses çıkışı.
- **Kısa Dalga Paraziti (Gürültü):** Beyaz gürültü ve 50Hz şebeke uğultusu mikslenmiş taktiksel arka plan paraziti.
- **Mors Kodu Vericisi:** Girilen metinleri dinamik olarak Mors koduna dönüştürüp, seçilen ton frekansı ve hız değerinde yayınlama.
- **Yayın Akışı Sıralayıcı (Sequence Builder):** Tanıtım anonslarını, mors kodlarını, yapay ses metinlerini sıraya ekleyerek kesintisiz ve sıralı çalma listeleri oluşturma.
- **PWA (Progressive Web App) Desteği:** Chrome veya Safari üzerinden Android/iOS cihazlara tam ekran, bağımsız bir mobil uygulama (ikonu ile birlikte) olarak yüklenebilme.

---

## 📁 Proje Yapısı

```text
orison/
├── scripts/
│   ├── orison                  # Ana Python yönetim CLI betiği
│   └── orison-broadcast        # PiFmRds ve DMA yönetimini yapan Bash betiği
├── sudoers/
│   └── orison                  # Şifresiz systemctl/ln izinleri için sudoers yapılandırması
├── systemd/
│   └── orison-web.service      # Flask web arayüzünü otomatik başlatan servis tanımı
├── web/
│   ├── app.py                  # Flask backend servisi ve API uç noktaları
│   ├── static/                 # Manifest, Service Worker ve PWA ikonları
│   └── templates/
│       └── index.html          # CRT retro konsol tasarımlı HTML5/JS frontend arayüzü
└── .gitignore                  # Git dışı bırakılacak geçici ses/log dosyaları
```

---

## ⚙️ Kurulum ve Dağıtım (Raspberry Pi Üzerinde)

### 1. Gereksinimlerin Yüklenmesi
Sistemde **Sox** (ses birleştirme/filtreleme için) ve **espeak-ng** (ses sentezleme için) kütüphanelerinin kurulu olması gerekir:
```bash
sudo apt-get update
sudo apt-get install sox libsox-fmt-all espeak-ng sshpass -y
```

Ayrıca Raspberry Pi üzerinde **PiFmRds** deposunun klonlanmış ve derlenmiş olması gerekmektedir. Proje varsayılan olarak `/home/host/PiFmRds/src/pi_fm_rds` yolundaki verici dosyasını arar.

### 2. Betiklerin ve Servisin Kurulması
CLI betiklerinin global komut olarak çalışabilmesi için symlink oluşturulmalı ve systemd servisi aktif edilmelidir:

```bash
# Betiklerin kopyalanması ve izinlerinin verilmesi
sudo cp scripts/orison /usr/local/bin/orison
sudo cp scripts/orison-broadcast /usr/local/bin/orison-broadcast
sudo chmod +x /usr/local/bin/orison /usr/local/bin/orison-broadcast

# Sudoers izninin verilmesi (Şifresiz verici başlatma/durdurma için)
sudo cp sudoers/orison /etc/sudoers.d/orison

# Systemd servisinin kurulması ve başlatılması
sudo cp systemd/orison-web.service /etc/systemd/system/orison-web.service
sudo systemctl daemon-reload
sudo systemctl enable orison-web.service
sudo systemctl start orison-web.service
```

Kurulum tamamlandıktan sonra terminale `http://station.local:8765` (veya Pi IP adresiniz) üzerinden erişebilirsiniz.

---

## 👨‍💻 Geliştirici Bilgisi

Bu proje, Raspberry Pi tabanlı taktiksel haberleşme ve simülasyon sistemleri için geliştirilmiş retro CRT temalı bağımsız bir radyo istasyon terminalidir.
