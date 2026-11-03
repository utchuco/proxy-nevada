# Diretrizes e Instruções do Projeto Proxy Nevada

## 1. Controle de Versão e GitHub
- **Sempre commitar e dar push para o GitHub (`origin/main`)** ao concluir qualquer tarefa que altere arquivos ou código do servidor/projeto.
- As mensagens de commit devem ser concisas e semânticas (ex: `fix(...)`, `feat(...)`, `chore(...)`).

## 2. Restrições Estritas de Operação (Blue Fleet)
- **NUNCA abrir ocorrências, ordens de serviço (O.S.) nem cadastrar peças ou veículos para testes** na API pública ou ambiente da Blue Fleet.
- Todas as validações e testes devem ser puramente de leitura (consultas e inspeções seguras), a menos que haja solicitação direta do usuário com dados oficiais.

## 3. Padrões de Formatação e Performance
- Datas de manutenções, peças e ocorrências devem ser sempre padronizadas no formato brasileiro `DD/MM/AAAA`.
- Priorizar algoritmos de parsing rápidos (como manipulação direta de strings) para não onerar o tempo de resposta das consultas.
