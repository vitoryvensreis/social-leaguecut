# Deploy inicial

O GitHub Actions atualmente sincroniza os arquivos, mas **não instala dependências e não reinicia serviços**.

Depois do primeiro checkout, no terminal como root, use o usuário do site:

```bash
sudo -u leaguecut-social-motor -H bash -c '
cd /home/leaguecut-social-motor/htdocs/social.leaguecut.com.br
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
'
```

Esse comando é provisório (ocupa o terminal). Configure uma unidade systemd de usuário ou serviço gerenciado do CloudPanel antes de ativar reinício automático. Confirme que a porta 8090 está livre.
