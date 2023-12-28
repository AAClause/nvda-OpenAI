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

#### Kısayollarla üretkenliğinizi artırın:

Arayüzle etkileşiminizi daha da geliştirmek için lütfen aşağıdakilere dikkat edin:

- Çok satırlı "Sistem", "Geçmiş" ve "İstem" alanları, klavye kısayolları kullanılarak hızla yürütülebilen komutlarla dolu bağlam menüleriyle donatılmıştır.
  Bu kısayollar, ilgili alan odakta olduğunda etkindir.
  Örneğin, 'j' ve 'k' tuşları, odak Geçmiş alanında olduğunda sırasıyla önceki ve sonraki mesajlara gitmenizi sağlar.
- Ayrıca arayüz, pencerenin tamamında etkili olan klavye kısayollarını içerir. Örneğin, 'CTRL + R' kaydı başlatır veya durdurur.

Tüm klavye kısayolları, karşılık gelen öğelerin etiketlerinin yanında görüntülenir.

#### Konuşma Modu Hakkında onay kutusu

Konuşma modu onay kutusu, sohbet deneyiminizi geliştirmek ve giriş belirteçlerini kaydetmek için tasarlanmıştır.

Etkinleştirildiğinde (varsayılan ayar), eklenti konuşma geçmişinin tamamını yapay zeka modeline iletir, böylece bağlamsal anlayışın iyileştirilmesini sağlar ve daha tutarlı yanıtlar sağlar. Bu kapsamlı mod, girdi jetonlarının daha yüksek tüketimine neden olur.

Bunun tersine, onay kutusu işaretlenmeden bırakıldığında yapay zeka modeline yalnızca mevcut kullanıcı istemi gönderilir. Bağlamsal kavrama ihtiyacını atlayarak ve diyalog geçmişi gerekli olmadığında girdi belirteçlerini koruyarak belirli soruları yönlendirmek veya farklı yanıtlar almak için bu modu seçin.

Bir oturum sırasında istediğiniz zaman iki mod arasında geçiş yapabilirsiniz.

#### 'Sistem' Alanı Hakkında

'Sistem' alanı, yapay zeka modelinin davranışına ve kişiliğine özel beklentilerinize uyacak şekilde ince ayar yapmak için tasarlanmıştır.

- **Varsayılan İstem**: Kurulumun ardından eklenti, kullanıma hazır bir varsayılan sistem istemi içerir.
- **Özelleştirilmiş**: Metni doğrudan alanın içinde değiştirerek sistem istemini kişiselleştirme özgürlüğüne sahipsiniz. Eklenti, kullandığınız son sistem istemini hatırlayacak ve iletişim kutusunu bir sonraki başlatışınızda otomatik olarak yükleyecektir. Bu davranış ayarlardan devre dışı bırakılabilir.
- **Sıfırlama Seçeneği**: Standart yapılandırmaya geri dönmek mi istiyorsunuz? 'Sistem' alanını zahmetsizce varsayılan değerine sıfırlamak için içerik menüsünü kullanmanız yeterlidir.

Lütfen sistem isteminin yapay zeka modelinin giriş verilerine dahil edildiğini ve jetonları buna göre tükettiğini unutmayın.

### Genel Komutlar

Bu komutlar, bilgisayarınızın herhangi bir yerinden eylemleri yürütmek için kullanılabilir. Bunları *Girdi Hareketleri* İletişim kutusunda *Open AI* Dalı altından yeniden atamak mümkündür.

- NVDA+e: Ekran görüntüsünü al ve betimle.
- NVDA+o: Geçerli gezgin nesnesini al ve onu betimle.
- Varsayılan olarak hiçbir hareket atanmayan komutlar:
    - Mikrofon sesini aç ve sesi metne çevir.

# Dahil Edilen Bağımlılıklar

Eklenti aşağıdaki temel bağımlılıklarla birlikte gelir:

- [openai](https://pypi.org/project/openai/): Openai API'si için resmi Python kütüphanesi.
- [markdown2](https://pypi.org/project/markdown2/): A fast and complete Python implementation of Markdown.
- [MSS](https://pypi.org/project/mss/): Saf python'da ctypes kullanan, ultra hızlı, çapraz platformlu çoklu ekran görüntüsü modülü.
- [Pillow](https://pypi.org/project/Pillow/): Görüntüyü yeniden boyutlandırmak için kullanılan Python Imaging Library'nin kullanıcı dostu çatalı.
- [sounddevice](https://pypi.org/project/sounddevice/): Python ile Ses Çalın ve Kaydedin.
