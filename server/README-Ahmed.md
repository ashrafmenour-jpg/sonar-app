# Sonar على سيرفر — تسليم لأحمد

**الهدف:** عقل Sonar يشتغل 24 ساعة بدل لابتوب/تابلت صاحي 69% من اليوم. نفس التطبيق بالظبط (web/index.html) شغال في Chromium headless على سيرفر، ومعاه watchdog ولوحة متابعة. **مفيش أوامر حقيقية** — Sonar نفسه مش بيبعت أوامر لأي منصة.

## 0) قبل أي حاجة: ارفع التحديثات على GitHub
Claude مش قادر يرفع على المستودع من عنده، فالـ 4 commits دول جاهزين في `sonar-patches/` جوه الملف المضغوط:
```bash
git clone https://github.com/ashrafmenour-jpg/sonar-app.git && cd sonar-app
git am /path/to/sonar-patches/*.patch      # 3.0.12 · 3.0.13 · 3.0.14 · server
git push origin main                        # بحساب ashrafmenour-jpg أو بصلاحية عليه
```
بعد الـ push: تبويب **Actions** في GitHub → لازم «Test Sonar page» يبقى ✅ أخضر. التطبيقات عند أشرف هتنزّل 3.0.14 لوحدها خلال 3 ساعات.

## 1) السيرفر
- **Hetzner Cloud CX22** (2 vCPU / 4 GB RAM / 40 GB) — منطقة **Falkenstein أو Helsinki**، Ubuntu 24.04. (~5 يورو/شهر). أي VPS بنفس المواصفات ينفع.
- **ممنوع منطقة أمريكا:** Binance بيرفض الـ IPs الأمريكية (HTTP 451). أوروبا كويسة.
- دومين فرعي: مثلاً `sonar.zedsglobal.com` → A record على IP السيرفر.

## 2) التركيب (حوالي 20 دقيقة)
```bash
# على السيرفر، كـ root
apt update && apt install -y docker.io docker-compose-v2 git ufw
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
git clone https://github.com/ashrafmenour-jpg/sonar-app.git /opt/sonar-app
cd /opt/sonar-app/server
cp .env.example .env && nano .env          # SONAR_PASS = باسورد طويل (اليوزر: sonar)
nano Caddyfile                              # غيّر sonar.example.com للدومين الحقيقي
mkdir -p data/import                        # ← حط هنا ملف «كل بياناتي» من التابلت (الخطوة 3) قبل أول تشغيل
docker compose up -d --build
docker compose logs -f sonar                # لازم تشوف: «Sonar اشتغل على السيرفر (نسخة …)»
```
افتح `https://<الدومين>` → يوزر `sonar` + الباسورد.

## 3) نقل تعلّم أشرف للسيرفر (مرة واحدة)
1. أشرف من التابلت: 🩺 ← «📦 صدّر كل بياناتي» → ملف `Sonar-كل-بياناتي-….json`.
2. حطه في `/opt/sonar-app/server/data/import/` ثم `docker compose restart sonar`.
3. اللوحة هتكتب «اتنقلت بيانات التابلت (N جزء)». الملف بيتنقل لـ `import/done/` علشان ما يتحملش تاني.

> بعد النقل فيه نسختين بيتعلموا لوحدهم (السيرفر والتابلت). **السيرفر هو المرجع** — ملفات التشخيص اللي تتبعت لـ Claude تبقى من اللوحة. التابلت يفضل شغال كعرض واحتياطي لحد المرحلة 2 (المزامنة).

## 4) اللوحة
| الرابط | فيه إيه |
|---|---|
| `/` | حالة السيرفر، المنصات واصلة ولا لأ، آخر الأحداث، صورة من التطبيق (بتتحدث كل دقيقة) |
| `/diagnosis` | آخر ملف تشخيص (كل 15 دقيقة) — ده اللي أشرف يبعته لـ Claude |
| `/learning` · `/all` | ملف التعلم · نسخة كاملة من كل البيانات |
| `/errors` | أخطاء الصفحة (console) |
| `/health` | من غير باسورد — للمراقبة |

## 5) التشغيل اليومي (أوتوماتيك)
- **تحديثات:** بيسحب من GitHub كل 20 دقيقة، ولو `web/version.json` اتغيّر بيعيد فتح الصفحة. مفيش حاجة تتعمل.
- **watchdog:** نبضة كل دقيقة؛ 3 نبضات فاشلة أو crash → المتصفح يتشغّل تاني. إعادة تشغيل يومية للذاكرة. البيانات في `data/profile` فمش بتضيع.
- **Docker:** `restart: unless-stopped` + healthcheck — لو السيرفر عمل reboot الكونتينر بيقوم لوحده.
- **نسخ احتياطي:** نسخة كاملة يوميًا في `data/backup/` لمدة 14 يوم.

## 6) المطلوب من أحمد بعد التركيب
1. **مراقبة:** UptimeRobot (مجاني) على `https://<الدومين>/health` كل 5 دقايق → تنبيه إيميل/تليجرام لو وقع.
2. **نسخ خارج السيرفر:** مرة في الأسبوع انسخ `data/backup/` لمكان تاني (Google Drive / S3 / rclone cron).
3. **أمان:** SSH بالمفاتيح بس (اقفل الدخول بالباسورد)، الـ firewall زي فوق، باسورد اللوحة طويل.
4. **أول 48 ساعة:** شوف اللوحة: «المنصات من السيرفر» كلها ✅؟ «أخطاء آخر ساعة» قريبة من صفر؟ «إعادة تشغيل المتصفح» مش بتزيد كل شوية؟ ابعت لقطة لأشرف.

## 7) أخطاء متوقعة وحلها
| العرض | السبب | الحل |
|---|---|---|
| Binance ❌ في اللوحة | منطقة السيرفر محظورة | انقل السيرفر لـ Falkenstein/Helsinki |
| الكونتينر بيقع كل شوية / OOM | ذاكرة قليلة | السيرفر 4GB على الأقل، `shm_size: 1gb` موجودة |
| «التخزين مليان» في الأخطاء | حد localStorage حوالي 10MB | ابعت ملف التشخيص لـ Claude — ده تعديل في Sonar مش في السيرفر |
| Caddy مش بيطلع شهادة HTTPS | الـ DNS لسه ما وصلش أو بورت 80 مقفول | استنى الـ DNS، `ufw allow 80` |
| اللوحة بتقول «stale» | الصفحة واقفة | الـ watchdog بيعيدها لوحده؛ لو استمر: `docker compose restart sonar` وابعت `/errors` |

أوامر مفيدة: `docker compose logs --tail 200 sonar` · `docker compose restart sonar` · `docker compose up -d --build` (بعد تعديل ملفات السيرفر نفسها).

## 8) اللي جاي بعد كده (نجهّز له من دلوقتي)
| المرحلة | إيه | مين |
|---|---|---|
| 2 | **مزامنة:** التطبيقات (تابلت/ماك) تبقى شاشة عرض لبيانات السيرفر بدل ما كل واحد يتعلم لوحده — API بسيط من السيرفر | Claude (كود) + أحمد (نشر) |
| 3 | **اختبار تاريخي:** job على السيرفر/GitHub بيشغّل استراتيجية Sonar المثبتة على بيانات من 2018 (هبوط 2018 و2022، صعود 2021، تذبذب) | Claude |
| 4 | **بوابة تحديثات آمنة:** التطبيقات تسحب من فرع `stable` اللي بيتحدّث بس بعد ما `Test Sonar page` ينجح | Claude + أحمد |
| 5 | **تنبيهات تليجرام** من السيرفر لأشرف (قرار جديد، صفقة اتقفلت، السيرفر وقع) | Claude + أحمد (bot token) |
| 6 | **تداول حقيقي (بعد شهادة الـ 9 شروط بس):** مفاتيح XT/KuCoin على السيرفر كـ secrets — صلاحية تداول **بدون سحب**، مقفولة على IP السيرفر | أحمد (المفاتيح والأمان) + Claude |
| 7 | نقل العقل من صفحة في متصفح لخدمة Node حقيقية لما الأساس يثبت | Claude |

## 9) الملفات
- `server/runner.py` — المشغّل (watchdog، تحديثات، ملفات، لوحة، نقل البيانات).
- `server/smoke_test.py` — اختبار الصفحة؛ بيشتغل في GitHub Actions مع كل تحديث (`.github/workflows/test.yml`).
- `server/Dockerfile` · `docker-compose.yml` · `Caddyfile` · `.env.example`.
