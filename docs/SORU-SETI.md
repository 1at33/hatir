# Hafıza eval soru setini oluşturma

Soru seti, bir hafıza aramasının hangi notları bulmasını ve hangilerinden kaçınmasını beklediğinizi kaydeder. Kendi kasanızdan seçtiğiniz örnekleri ajanınız için sorgulara dönüştürün; bu belge yöntem ve biçimi açıklar.

## Setler ve ölçüler

- **Gerileme:** Sistemin daha önce bulabildiği bilinen sorular. Bu sette beklenen notların ilk beşte kalması hedeflenir; kaçırma, çalışan bir davranışın gerileme sinyalidir.
- **Yetenek:** Zor, dolaylı veya anlamsal sorular. Bunlar gelişme alanını gösterir; soru sözcükleri nottakilerden farklı olabilir.
- **Olumsuz:** Arşivlenmiş, eski veya özellikle kullanılmaması gereken notları sınar. Bir yasak yol ilk beş kaynak arasında çıkarsa ihlal sayılır. Bu ayrı bir `set` değeri değildir: `beklenen` boş ve `yasak` dolu soru olumsuz kabul edilir. Böyle bir soru isabet ortalamasına girmez.

`hit@k`, bir sorunun beklenen yollarından en az birinin, tekilleştirilmiş sıralı kaynakların ilk `k` öğesinde bulunması halinde 1, yoksa 0 değerini alır. Rapor, `hit@1`, `hit@3` ve seçilen `k` için hit değerini; genel ve `kelime`/`anlam` gruplarının ortalaması olarak verir. Hafıza puanlayıcısındaki `hit@5`, ilgili sette beklenen listesi bulunan ve hatasız sorularda ilk beş sıraya girenlerin oranıdır. Soru başına ilk doğru sonucun sırası da raporlanır; MRR, bu sıranın tersinin (`1/sıra`) ortalamasıdır, sonuç yoksa katkı sıfırdır. Olumsuz ihlal ayrı olarak ilk beşte yasak yola rastlanan soru adedidir.

`hatir-puanla` gerileme ve yetenek setleri için `hit@k` ve MRR'yi, olumsuz sorular için ihlal adedini ayrı raporlar. Set ayrıştırıcısı (`hatir-ayir`) gerileme/yetenek için çalışır; olumsuz soruların `beklenen` alanı boş olduğu için daima gerileme sınıfında kalır.

## JSONL şeması

Dosya UTF-8 JSONL'dir: her dolu satır bir JSON nesnesidir. Alan adları, paket örneğindeki alanların birleşimiyle aynıdır: `id`, `soru`, `proje`, `beklenen`, `tur`, `set`, `yasak`. Ek alanlar puanlayıcı sözleşmesinin parçası değildir. Aşağıdaki örnek değerler yer tutucudur.

| Alan | Tür | Zorunlu | Örnek değer | Açıklama |
| --- | --- | --- | --- | --- |
| `id` | metin | evet | `q-001` | Boş olmayan ve dosya içinde tekrarlanmayan soru kimliği. |
| `soru` | metin | evet | `Yedekleme kararı hangi hedefi seçmişti?` | Aramaya verilecek soru; boş olamaz. |
| `proje` | metin veya `null` | hayır | `ornek-proje` | Proje kapsamı; yok veya `null` ise proje filtresi kullanılmaz. |
| `beklenen` | metin dizisi | evet | `["notlar/karar-x.md"]` | Doğru sayılacak kasa içi yollar. Boş dizi yalnız `yasak` dolu olumsuz soruda geçerlidir. |
| `yasak` | metin dizisi | hayır | `["notlar/arsiv-y.md"]` | İlk `k` içinde görünmemesi gereken kasa içi yollar; verilmezse boş kabul edilir. |
| `tur` | metin (`kelime` veya `anlam`) | evet | `anlam` | `kelime` anahtar terimleri kullanan, `anlam` aynı bilgiyi başka sözcüklerle soran türdür. |
| `set` | metin (`gerileme` veya `yetenek`) | hayır | `yetenek` | Olumlu sorunun izleneceği set; verilmezse ölçüt set ortalamalarına alınmaz. |

`beklenen` ve `yasak` içindeki her yol kasa köküne göreli, ileri eğik çizgili olmalıdır; örneğin `notlar/karar-x.md`. Mutlak yol, `..` bileşeni ve ters eğik çizgi kullanmayın. En az bir `beklenen` yolu veya `yasak` yolu bulunmalıdır.

Örnek satırlar:

```jsonl
{"id":"q-001","soru":"Yedekleme kararı hangi hedefi seçmişti?","proje":"ornek-proje","beklenen":["notlar/karar-x.md"],"tur":"kelime","set":"gerileme"}
{"id":"q-002","soru":"Veri kaybına karşı hangi önlem kararlaştırılmıştı?","proje":"ornek-proje","beklenen":["notlar/karar-x.md"],"tur":"anlam","set":"yetenek"}
{"id":"q-003","soru":"Eski ayar neydi?","proje":null,"beklenen":[],"yasak":["notlar/arsiv-y.md"],"tur":"kelime","set":"gerileme"}
```

## Seti hazırlama

1. **Kasadan örnekleyin.** Farklı konulardan ve mümkünse farklı not türlerinden, ajanın araması beklenen notları seçin. Her sorunun yanıtını insan olarak doğrulayabileceğiniz bir veya daha fazla `beklenen` yol belirleyin.
2. **Soruyu nottan türetin, kopyalamayın.** Sorgu, notun ayırt edici cümlesini aynen taşımamalı; aksi halde kelime eşleşmesi anlamsal bulma yeteneğini olduğundan iyi gösterebilir. Soruyu kullanıcının gerçekten sorabileceği şekilde yazın.
3. **Yasak yolları açıkça işaretleyin.** Arşivlenmiş veya geçersiz bir yanıtı cazip kılabilecek notları `yasak` listesine ekleyin. Yasak yol doğru yanıt değilse `beklenen` listesine koymayın. Olumsuz soru için `beklenen` boş, `yasak` dolu olsun.
4. **Soru türlerini ve güçlüğü dengeleyin.** `kelime` soruları anahtar terimleri sınar; `anlam` soruları başka ifadelerle aynı bilgiye ulaşmayı sınar. Her konunun yalnızca bir türde yığılmamasına dikkat edin.
5. **Setleri ayırın.** Bugün güvenilir biçimde bulunan olumlu soruları `gerileme`, henüz zorlanan ya da anlamsal gelişimi izlenecek soruları `yetenek` olarak işaretleyin. Olumsuz soruları kendi `set` etiketiyle göstermeyin; yasak listesi ve boş beklenen listesiyle tanımlanırlar. Puanlayıcı `--set` filtresinde yalnız `gerileme` ve `yetenek` seçeneklerini sunar.
6. **Boyutu amaca göre seçin.** Tek bir sonuçtan etkilenmeyecek kadar çeşitli soru seçin; bakımını yapamayacağınız kadar geniş bir set kurmayın. Her konu ve soru türünün anlamlı biçimde temsil edilmesi, sabit bir soru adedinden daha yararlıdır.
7. **Sürümleyin ve dondurun.** Her ölçüm dönemi için soru kimliklerini ve soru metinlerini sabit tutun. Değişiklikleri gözden geçirip yeni sürüm olarak kaydedin; eski sonuçlarla kıyas yaparken aynı soru kimliği kümesini ve aynı sorgu anlamını koruyun.
8. **İnsan gözden geçirmesi yapın.** Başka bir kişi beklenen yanıtı ve yolları kasadan doğrulasın; sorunun not cümlesini sızdırmadığını, yasakların gerçekten yasak olduğunu ve tür/set etiketlerinin uygunluğunu kontrol etsin.

## hatir ile ölçme

```sh
hatir-puanla --vault <kasa> --sorular <sorular.jsonl> --k 5
```

Belirli olumlu seti seçmek için `--set gerileme` veya `--set yetenek` ekleyin. Olumsuz denetim, dosyadaki yasak listeli sorulardan yapılır. İki sonuç dosyasını gerileme/yetenek olarak karşılaştırmak için `hatir-ayir --sorular <sorular.jsonl> --sonuc A.json --sonuc B.json` kullanın.

Ölçüm sonuçları soruların ve kasa yollarının bilgisini içerebilir; soru setini ve sonuçları repoya eklemeyin.
