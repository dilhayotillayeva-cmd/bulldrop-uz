
# BULLDROP Telegram Bot

## Funksiyalar
- 🎁 5x5 qutilar
- 🪙 Quti ochish narxi: 5 tanga
- Har bir roundda 25 qutidan faqat 1 tasi yutuqli
- Yutuqsiz quti: "😔 Afsus, omad kelmadi! Kelasi safar aniq o‘xshaydi 😉"
- Referal: yangi foydalanuvchi uchun +1 tanga
- 👤 Profil
- 🏆 TOP — joriy tanga balansi bo‘yicha
- 🎟️ Promokodlar
- 📢 Majburiy kanal obunasi
- 🛠 Admin panel
- 💾 SQLite + WAL; Render persistent disk bilan ma'lumotlar saqlanadi

## Render
1. GitHub'ga shu fayllarni yuklang.
2. Render → New → Blueprint yoki Web Service emas, **Background Worker** sifatida `render.yaml` orqali deploy qiling.
3. Environment Variables:
   - `BOT_TOKEN` — @BotFather bergan token
   - `ADMIN_IDS` — admin Telegram ID, masalan `123456789`
4. Persistent Disk `render.yaml` ichida `/var/data` ga ulangan. SQLite shu yerda saqlanadi.

## Majburiy kanal
Admin `/admin` → 📢 Majburiy kanallar → ➕ Kanal qo‘shish.
Bot kanal ichida admin bo‘lishi kerak. Public kanal uchun `@username`, private kanal uchun `-100...` chat ID ishlatish mumkin.

## Muhim
Bot foydalanuvchining **har bir menyu tugmasi va callback** oldidan majburiy obunani tekshiradi. Obuna bo‘lmagan foydalanuvchi bot funksiyalaridan foydalana olmaydi.

## Admin
`/admin` faqat `ADMIN_IDS` ichidagi Telegram ID'larga ishlaydi.

## Quti rasmi
`quti_tanla.png` qutilar menyusi ochilganda 25 ta interaktiv quti tugmalarining ustida ko‘rsatiladi.
