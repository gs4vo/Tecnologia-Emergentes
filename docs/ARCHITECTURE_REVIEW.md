# Revisão da Arquitetura

## Frontend

O frontedn está localizado nas pastas 'templates' e 'static'.

A interface não realiza apenas exibição de dados, contendo também lógica de interação e comunicação assíncrona

Funções indentificadas:
- 'refreshEvents()'
- 'refreshStatuses()'
- 'sendChat()'
- 'updateHistory()'

Apesar disso, a maior parte da lógica escontrada ainda está relacionada ao comportamento da interface.

---

## Backend/API

O backend encontra-se excessivamente centralizado no arquivo 'app.py'

O arquivo concentra:
- rotas;
- inicialização da aplicação;
- acesso ao banco;
- chamadas ao modelo YOLO;
- processamento;
- integração com serviços.

Isso caracteliza alto acoplamento arquitetural.

---

## Banco de Dados

Não existe uma camada dedicada de persistência.

O acesso aos dados está diretamente no 'app.py', dificultando manutenção e escalabilidade.

---

## Camada de IA

A lógica do modelo YOLO está diretamente integrada ao 'app.py'.

Embora exista uma pasta 'models', ela não centraliza o processamento da IA.

Recomenda-se mover a interferência para uma camada de serviço própria.

---

## Serviços internos

A pasta 'services' existe, porém não centraliza efetivamente as responsabilidades do sistema.

Grande parte da lógica permanece no backend principal.

---

## Web Scraping

A camada de scraping será implementada como serviço parcialmente separado, isso por ainda estar bastante acoplado ao 'app.py'.

O arquivo 'commodity_scraper.py' encapsula adequadamente lógica de coleta, parsing, cache e tratamento de erro em uma classe própria (commodityScraperService), reduzindo acoplamento e facilitando manutenção.

Entretanto ainda é necessário verificar como essa camada é integrada ao 'app.py' e se existe alguma dependência direta excessiva do backend principal.

---

## Conclusão

O projeto apresenta funcionamento adequado, porém possui problemas arquiteturais relacionados principalmente à ccentralização excessiva no 'app.py'.

Os principais problemas identificados foram:
- ausência de modularização adequada;
- acoplamento entre backend, IA e persistência;
- pouca separação de responsabilidades;
- utilização limitada da camada de serviços.

Apesar disso, a estrutura da diretória demonstra tentativa inicial de organização arquitetural.