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

> [!IMPORTANT]
> **Kullanıcı Adı Notu:** Projedeki varsayılan yollar `/home/host/...` kullanıcısına göre yapılandırılmıştır. Eğer yeni Pi'deki kullanıcı adınız `host` değilse (örneğin standart `pi` ise), kuruluma başlamadan önce **Adım 4**'teki kullanıcı adı güncelleme komutunu mutlaka çalıştırmalısınız.

### Adım 1: Temel Bağımlılıkların Yüklenmesi
Yeni Pi üzerinde terminali açın ve ses sentezleme, filtreleme ve web servis araçlarını yükleyin:
```bash
sudo apt-get update
sudo apt-get install git sox libsox-fmt-all espeak-ng python3-pip python3-flask -y
```

### Adım 2: PiFmRds Çekirdeğinin Derlenmesi
Radyo dalgalarını üreten çekirdek kütüphaneyi Pi üzerine klonlayıp derleyin:
```bash
# Depoyu kullanıcı dizinine klonlayın
cd ~
git clone https://github.com/ChristopheJacquet/PiFmRds.git

# Derleme işlemini başlatın
cd PiFmRds/src
make
```
*Bu işlem sonucunda `/home/KULLANICI_ADI/PiFmRds/src/pi_fm_rds` yolunda çalıştırılabilir çekirdek dosya oluşacaktır.*

### Adım 3: ORISON Projesinin GitHub'dan Çekilmesi
Kendi oluşturduğunuz GitHub deposunu Pi'ye klonlayın:
```bash
cd ~
git clone https://github.com/cagriakyurt/orison-station.git station
```

### Adım 4: Kullanıcı Adı Güncellemesi (Gerekliyse)
Eğer yeni Pi'deki kullanıcı adınız `host` **değilse** (örneğin `pi` ise), proje dosyalarındaki tüm `host` yollarını yeni kullanıcı adınızla değiştirmek için şu komutu çalıştırın:
```bash
cd ~/station
# 'host' yerine kendi kullanıcı adınızı yazın (örn: 'pi')
find . -type f -not -path '*/.*' -exec sed -i 's/home\/host/home\/pi/g' {} +
```

### Adım 5: Sistem Ayarlarını ve Servisleri Kurun
CLI betiklerinin global komut olarak tanımlanması, çalışma izinleri ve web panelinin Pi açıldığında otomatik başlaması için servis kurulumu:

```bash
cd ~/station

# 1. Betiklerin kopyalanması ve izinlerinin verilmesi
sudo cp scripts/orison /usr/local/bin/orison
sudo cp scripts/orison-broadcast /usr/local/bin/orison-broadcast
sudo chmod +x /usr/local/bin/orison /usr/local/bin/orison-broadcast

# 2. Şifresiz verici kontrolü (pkill/systemctl) yetkisini sudoers'a tanımlayın
sudo cp sudoers/orison /etc/sudoers.d/orison
sudo chmod 440 /etc/sudoers.d/orison

# 3. Web panelinin Pi açıldığında otomatik başlaması için servisi kurun
sudo cp systemd/orison-web.service /etc/systemd/system/orison-web.service
sudo systemctl daemon-reload
sudo systemctl enable orison-web.service
sudo systemctl start orison-web.service
```

### Adım 6: Donanım ve Erişim
1. Raspberry Pi'nizin **GPIO 4** pinine (fiziksel Pin 7) anten görevi görmesi için yaklaşık 20-30 cm boyunda basit bir kablo bağlayın.
2. Tarayıcınızdan `http://yeni-pi-ip-adresi:8765` (veya `http://station.local:8765`) adresine giderek kontrol panelinize erişebilirsiniz.

---

## 👨‍💻 Geliştirici Bilgisi

Bu proje, Raspberry Pi tabanlı taktiksel haberleşme ve simülasyon sistemleri için geliştirilmiş retro CRT temalı bağımsız bir radyo istasyon terminalidir.
