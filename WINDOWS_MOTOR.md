# Social LeagueCut — motor Windows (30 dias)

O painel FastAPI, o MySQL e o Google Drive permanecem na VPS. O Windows executa o motor Instagram na conexão residencial.

**Instalador Windows:** distribuído separadamente em ZIP nesta conversa, para não registrar tokens/sessões/senhas no GitHub. O pacote contém instalar.bat, instalar.ps1 e aplicação Python (GUI Tkinter) e inicia ao entrar no Windows.

## Ativar API na VPS

Como root:
```bash
cd /home/leaguecut-social-motor/htdocs/social.leaguecut.com.br
if ! grep -q '^MOTOR_API_TOKEN=' .env; then
  TOKEN=$(openssl rand -hex 32)
  printf '\nMOTOR_API_TOKEN=%s\n' "$TOKEN" >> .env
  echo "Copie este token com cuidado para o app Windows: $TOKEN"
  unset TOKEN
fi
chown leaguecut-social-motor:leaguecut-social-motor .env
chmod 600 .env
systemctl restart social-leaguecut
systemctl disable --now social-leaguecut-metrics.timer 2>/dev/null || true
```

A API **não** expõe MySQL ou Google OAuth ao Windows. Usa HTTPS e `Authorization: Bearer` com token longo gerado na VPS.

## Endpoints

- `GET /api/motor/health`: conexão autenticada
- `GET /api/motor/accounts`: contas públicas cadastradas
- `POST /api/motor/accounts/{id}/status`: status fornecido pelo motor após autenticação
- `POST /api/motor/jobs/claim`: aceita apenas `ready_accounts`, escolhe uma publicação vencida até 15 minutos
- `GET /api/motor/media/{drive_id}`: streaming de vídeos catalogados no Drive; máximo 500 MB
- `POST /api/motor/jobs/{id}/result`: confirma link Instagram ou estado `needs_review`
- `GET /api/motor/metrics/pending`: lista publicações com consulta de métricas pendente
- `POST /api/motor/metrics`: registra um snapshot diário

### Segurança e comportamento

- Nenhuma tentativa de Instagram é executada pela VPS.
- O painel não solicita senhas Instagram.
- Proxies e autenticações remotas foram removidos das rotas ativas; tabelas antigas foram preservadas por segurança até limpeza explicitamente solicitada.
- O Windows salva senhas no Gerenciador de Credenciais e sessões localmente criptografadas; não há garantia de revalidação permanente das 16 contas.
- `publishing` não é retomado automaticamente após crash: transiciona a `needs_review` em 90 minutos.
- Posts atrasados há mais de 15 minutos viram `missed` ao motor reconectar.
- Instagram privado/Instagrapi pode causar desafios, perdas de sessão e restrições. Iniciar com **uma conta**.

### Configuração da campanha

No painel: conectar Drive, sincronizar pasta, escrever legendas variadas, escolher janela (ex. 10–19h), gerar os 30 dias. Para cada perfil, o programa Windows primeiro precisa armazenar uma sessão autenticada manualmente.
