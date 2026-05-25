1. Detecção e salvamento de eventos

O que o código fazia: ele analisava os frames da câmera, identificava objetos e salvava um evento quando encontrava uma classe importante.

Qual era o problema: o mesmo objeto podia ser detectado várias vezes seguidas, gerando alertas repetidos. Também havia risco de salvar um evento por engano em uma única detecção isolada.

O que foi melhorado: o código passou a confirmar a detecção só depois de ela aparecer em vários frames seguidos e também passou a esperar um tempo antes de gerar outro alerta igual.

Por que isso é melhor: evita repetição, reduz falsos alertas e deixa o sistema mais confiável para uso real.

Alteração: app.py


2. Envio de dados para o Gemini

O que o código fazia: ele montava o contexto com os eventos recentes e enviava a pergunta do usuário para a IA responder.

Qual era o problema: a chamada podia falhar se o modelo principal não estivesse disponível ou se o nome do modelo estivesse errado.

O que foi melhorado: o código passou a organizar melhor o contexto, normalizar o nome dos modelos e testar modelos de reserva quando o principal não funciona.

Por que isso é melhor: o sistema fica mais estável, mais fácil de manter e com mais chance de responder sem erro.

Alteração: app.py

3. Histórico do chat no frontend

O que o código fazia: ele mostrava o chat na tela e enviava as perguntas do usuário para o backend.

Qual era o problema: o chat podia ficar confuso e o usuário podia mandar várias mensagens enquanto a resposta ainda estava sendo processada.

O que foi melhorado: o frontend passou a guardar um histórico curto das conversas, mostrar as mensagens na tela e desativar o botão enquanto a IA responde.

Por que isso é melhor: a conversa fica mais organizada, o uso fica mais simples e a interface fica mais fácil de entender.

Alteração: index.html