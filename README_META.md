# Social LeagueCut — Meta Studio (Instagram Login)

A página https://social.leaguecut.com.br/ é agora um painel de **publicações e análise por API oficial**. O motor Playwright/Windows não faz parte do servidor ativo. O código antigo permanece apenas no histórico do Git por segurança.

## O que funciona
- Login administrativo com sessão HTTPS e CSRF.
- Cadastro **manual** do access token gerado no dashboard do Meta Developers; valida `/me` e identifica `username` + ID correto.
- Token criptografado em `meta_accounts.token_encrypted` (Fernet). Nenhum token é impresso na interface ou nos logs.
- Contas ativas e pausadas, atualização de token da mesma conta.
- Upload MP4 para mídia pública no mesmo domínio, ou fornecimento de URL HTTPS acessível.
- Agendamento para 1 ou mais contas, com intervalo em minutos, horário local America/Fortaleza.
- Worker no próprio serviço FastAPI, a cada 45 segundos, usando API de Instagram Login (`graph.instagram.com`):
  1. Cria um contêiner Reel (`/{id}/media`).
  2. Confere `status_code=FINISHED` (`/{container-id}`).
  3. Publica uma única vez (`/{id}/media_publish`).
  4. Persiste media ID, permalink e data.
- Visões e interações por Reel via Graph API, até 12 atualizações por ciclo para não concentrar requisições. Dados indisponíveis permanecem N/D.
- Estados `needs_review` em publicações com resultado incerto: **sem republicação automática**.
- Tabelas `meta_*` distintas das legadas: nenhum dado antigo é apagado durante a migração.

## Configuração no Meta Developers
Use **Instagram API with Instagram Login**, contas profissionais do Instagram **Business ou Creator**, registradas com acesso/teste permitido no app em modo desenvolvimento. Requisite:
- `instagram_business_basic`
- `instagram_business_content_publish`
- `instagram_business_manage_insights`

No dashboard do app, vá em Instagram → Generate access tokens → Generate token. Autorize todas as permissões. Copie o token e cole em **Adicionar token**. Testers têm de aceitar o convite quando aplicável. Tokens em modo desenvolvimento **não permitem publicar em contas externas arbitrárias**.

## Variáveis de ambiente (somente VPS; NUNCA no Git)
Reaproveita: `ADMIN_PASSWORD`, `SESSION_SECRET`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
Novas opcionais:
- `META_TOKEN_KEY`: chave Fernet gerada por `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Recomendada: fica independente de `SESSION_SECRET`. Se não configurada, uma chave é derivada da SESSION_SECRET; **não altere esse segredo sem migrar tokens**.
- `META_GRAPH_VERSION=v25.0`
- `META_MEDIA_DIR=/home/leaguecut-social-motor/media` (diretório persistente fora do Git)
- `META_PUBLIC_URL=https://social.leaguecut.com.br`
- `META_WORKER_ENABLED=true`

Nginx/CloudPanel deve permitir `client_max_body_size 256m` e manter `/media/{id}.mp4` acessível sem autenticação para que a Meta baixe o arquivo. **Qualquer pessoa que conheça a URL pode acessar o vídeo**; só envie mídia apropriada para divulgação.

## Validade dos tokens
Tokens são gerados manualmente. O armazenamento não estende sua validade. Confirme a expiração no Meta Developers e **cole um novo token** quando necessário; o serviço não se baseia em datas estimadas nem promete renovação automática.

## Limitações de produção
- Validação real depende de informar um token de teste válido e fazer **uma publicação de teste** no Instagram; testes unitários não conseguem comprovar que a API aceitará o perfil/vídeo.
- Reels são publicados quando o worker chega à hora, não são “agendados” nos servidores da Meta. Precisa VPS ativa.
- O servidor usa um worker de polling por processo FastAPI. Em múltiplos processos, as transições de status no MySQL evitam duplicidade nas etapas críticas; prefira **um processo Uvicorn** para a primeira fase.
- Caso a Meta aceite a publicação mas a resposta se perca, o post fica em `needs_review` e exige conferência manual antes de novo agendamento.
- Apenas métricas permitidas pelo token podem ser lidas.
- O antigo banco é preservado, pois apagar tabelas ou gravações automaticamente seria destrutivo.

## Nova interface — conexão, agendamento e métricas
- Tela **Perfis conectados**: nome, username, avatar, tipo do perfil, seguidores, seguindo, total de publicações, dados diários disponíveis e data da última sincronização.
- **Conectar conta**: depois de cadastrar o token manualmente, tenta importar os 25 conteúdos recentes. O botão **Sincronizar dados** de cada perfil importa até 50 itens, sem apagar conteúdos previamente salvos.
- **Novo Reel**: escolha um MP4 e o upload inicia automaticamente. O URL HTTPS é preenchido pelo sistema em campo oculto; campo de URL manual é uma opção avançada. Legenda, seleção de contas e horário continuam disponíveis.
- **Agendamento em lote**: múltiplos arquivos, sequência por dia e intervalo entre perfis.
- **Posts e Analytics**: mídia importada do Instagram + publicações feitas pelo Studio, miniaturas, permalink, status, visualizações, curtidas, comentários, alcance, salvamentos e compartilhamentos quando a API fornecer. Ranking top 5 por visualizações conhecidas.
- **Métricas de conta**: leituras diárias disponíveis via Insights; o painel mostra o dia de medição e 'N/D' quando a Meta não retornar o indicador.
- **Histórico**: tabela `meta_metric_history` com uma amostra diária por mídia. Leituras das mídias publicadas/importadas são feitas pelo worker periodicamente (até 12 posts por ciclo; cada um elegível após 6 horas).
- **Privacidade e idempotência**: tokens seguem cifrados e ausentes da interface, posts importados são vinculados pelo ID de mídia da Meta para não duplicar. As tabelas antigas continuam intactas.

### Como testar
1. Entre em https://social.leaguecut.com.br/ com o usuário admin.
2. Em **Seus perfis Instagram**, clique em **Sincronizar dados** para a conta de teste já cadastrada.
3. Confira se nome, quantidade de posts e miniaturas são preenchidos; campos vazios podem depender de permissões.
4. Em **Criar e agendar Reel**, escolha o MP4 e espere o aviso 'Vídeo enviado e pronto para agendar'. **Não é preciso preencher a URL**.
5. Escolha um horário futuro, faça uma única publicação de teste e confira a postagem no Instagram antes de distribuir em lote.

> Importante: a execução do GitHub Actions testa importação, templates e saúde do serviço, não substitui uma prova com token real. Em modo desenvolvimento o token só funciona para perfis autorizados. Na Meta, habilite as permissões adequadas para conteúdo, gerenciamento de perfil e Insights. Algumas métricas não são disponibilizadas para todos os conteúdos, contas ou períodos.
