arte 2 — Revisão de Segurança
O grupo deverá identificar riscos de segurança no projeto. Alguns pontos para observar:

Existem senhas, tokens ou chaves diretamente no código?
Não, senhasm tokens e chaves são salvas em um .env privado na máquina local.

As rotas da API estão abertas sem validação?
Não, existe  validação nas rotas da API.

Os dados enviados pelo usuário são validados antes de serem processados?
Sim, os daods enviados pelo usuário passam por validação antes do processamento.

Existe risco de SQL Injection, exposição de dados ou acesso indevido?
Não, inputs de outputs passam por validções antes de serem processados ou retornados ao usuário, o sistema retorna uma mensagem falando que o input está fora
do escopo de execução dele.

O sistema trata erros de forma segura ou mostra mensagens técnicas demais ao usuário?
O sistema mostra erros de forma simples e compreensível para o usuário.

Caso o projeto use IA, scraping ou upload de arquivos, o grupo também deve avaliar se existe risco de processar dados maliciosos ou fontes não confiáveis.
Atualmente não existem riscos, tendo em vista que a IA está com uma validação de input e não processa dados fora do escopo dela e o webscraping está definido
manualmente para apenas um site, porém, em caso de evolução do projeto, seja por evolução do escopo da IA ou por evoulução do escopo do webscraping, como
um webscraping dinâmico, seria necessário criar camadas adicionais de segurança como captchas e validação de fontes usadas. 