# Social LeagueCut

Plataforma temporária (30 dias) de planejamento, agendamento e monitoramento de publicações no Instagram.

## Stack prevista
- Python 3.12 / FastAPI
- Jinja2, HTML, CSS e JavaScript
- MySQL
- Worker de publicação separado do servidor web
- Integrações opcionais: Instagrapi, proxy Webshare e alertas WPPConnect

## Infraestrutura
- Domínio: https://social.leaguecut.com.br
- VPS: Hostinger / Ubuntu 24.04 / CloudPanel
- Deploy: checkout controlado no diretório exclusivo do site
- Credenciais exclusivamente em variáveis de ambiente, fora do Git

## Segurança
- Nunca versionar .env, senhas, sessões, tokens ou vídeos enviados.
- Uma publicação deve ser testada inicialmente com conta de teste.
- A API privada do Instagram não é oficial e pode sofrer limitações ou restrições.
- Não iniciar novas tentativas automaticamente quando houver desafio de autenticação ou restrição.

## Situação
Repositório inicializado. Configuração da VPS e aplicação ainda pendentes.
