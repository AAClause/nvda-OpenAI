# Open AI NVDA Eklentisi

Bu eklenti, Open AI API'nin yeteneklerini iş akışınıza sorunsuz bir şekilde entegre etmek için tasarlanmıştır. Kapsamlı bir metin oluşturmak, metinleri hassas bir şekilde çevirmek, belgeleri kısaca özetlemek ve hatta görsel içeriği yorumlayıp açıklamak istiyorsanız, bu eklenti hepsini kolaylıkla yapar.

## Kurulum Adımları:

1. İlk önce [Son sürüm için](https://github.com/aaclause/nvda-OpenAI/releases) sayfasına gidiyoruz.
2. Sağlanan bağlantıdan en son sürümü indiriyoruz.
3. Eklentiyi yüklüyoruz.

## Kullanım Önkoşulları

OpenAI NVDA eklentisinin tam işlevselliğinden yararlanmak için OpenAI'den bir API anahtarı gereklidir. Kurulum için şu adımları izliyoruz:

1. [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys) adresinden OpenAI hesabına kaydolarak bir API anahtarı ediniyoruz.
2. API anahtarı hazır olduğunda yapılandırma için iki seçeneğiniz vardır:
   - NVDA ayarlar iletişim kutusu aracılığıyla:
     1. NVDA menüsüne erişiyor ve "Tercihler" alt menüsüne gidiyoruz.
     2. "Ayarlar" iletişim kutusunu açıyor ve "Open AI" kategorisini seçiyoruz.
     3. Sağlanan alana API anahtarımızı giriyor ve onaylamak için "Tamam"ı tıklıyoruz.
   - Ortam değişkenlerini kullanma:
     1. Sistem Özelliklerini açmak için 'Windows+Duraklat'a basın.
     2. "Gelişmiş sistem ayarları"na tıklayın ve "Ortam Değişkenleri"ni seçin.
     3. "Kullanıcı değişkenleri" altında yeni bir değişken oluşturun:
         1. "Yeni"ye tıklayın.
         2. Değişken adı olarak `OPENAI_API_KEY` girin ve değer olarak API anahtarınızı yapıştırın.
     4. Değişikliklerinizi kaydetmek için "Tamam"ı tıklayın.

Artık OpenAI NVDA eklentisinin özelliklerini keşfetmeye hazırsınız!

## Eklenti Nasıl Kullanılır

### Ana Özelliklere Erişim

Eklentinin işlevselliği, 'NVDA+g' kısayolu kullanılarak açılabilen merkezi bir iletişim kutusunda bulunur. Bu iletişim kutusu eklentinin özelliklerinin çoğuna erişim sağlayarak şunları yapmanıza imkan verir:

- Yapay zeka modeliyle sohbete katılın.
- Görüntü dosyalarından görüntülerin betimlemelerini alın.
- Ses dosyalarından konuşulan içeriği yazıya aktarın.
- İstemde yazılı metni seslendirmek için metinden konuşmaya özelliğini kullanın.

#### Ana iletişim kutusundaki komutlar

Farklı öğeler için ana iletişim kutusunda bazı komutlar mevcuttur.

- İstem Alanına odaklanıldığında:
	- `Ctrl+Enter`: Girilen metin gönderilir.
	- 'Ctrl+Yukarı Ok': En son girilen istemi alıp gözden geçirmek veya yeniden kullanmak için geçerli alana yerleştir.
- Geçmiş Alanına odaklandığında:
	- `Alt+Sağ Ok`: Kullanıcı metnini isteme kopyala.
	- `Alt+Sol Ok`: Asistanın yanıtını sisteme kopyala.
	- 'Ctrl+C': İmlecin konumuna bağlı olarak asistanın yanıtını veya kullanıcının metnini kopyala.
	- `Ctrl+Shift+Yukarı Ok`: Geçerli bloğun üzerindeki kullanıcının veya asistanın metin bloğuna git.
	- `Ctrl+Shift+Aşağı Ok`: Geçerli bloğun altındaki kullanıcının veya asistanın metin bloğuna git.

### Genel Komutlar

Bu komutlar, bilgisayarınızın herhangi bir yerinden eylemleri yürütmek için kullanılabilir. Bunları *Girdi Hareketleri* İletişim kutusunda *Open AI* Dalı altından yeniden atamak mümkündür.

- `NVDA+e`: Ekran görüntüsünü al ve betimle.
- `NVDA+o`: Geçerli gezgin nesnesini al ve onu betimle.

# Dahil Edilen Bağımlılıklar

Eklenti aşağıdaki temel bağımlılıklarla birlikte gelir:

- [OpenAI](https://pypi.org/project/openai/): Openai API'si için resmi Python kütüphanesi.
- [MSS](https://pypi.org/project/mss/): Saf python'da ctypes kullanan, ultra hızlı, çapraz platformlu çoklu ekran görüntüsü modülü.
- [sounddevice](https://pypi.org/project/sounddevice/): Python ile Ses Çalın ve Kaydedin.
