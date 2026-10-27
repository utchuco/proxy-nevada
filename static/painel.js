/**
 * Painel de Gestão e Aprovação de Checklists - Locadora Nevada
 * Script cliente responsável por monitoramento OCR, validações em tempo real e envio de ocorrências.
 */

const CLASSES_POR_TIPO = {
    "PreventiveMaintenance": [
        { id: "PreventiveMaintenanceQuilometerRevision", label: "Revisão por Quilometragem (KM)" },
        { id: "PreventiveMaintenanceTimeRevision", label: "Revisão por Tempo" },
        { id: "PreventiveMaintenanceOilExchange", label: "Troca de Óleo e Filtros" },
        { id: "PreventiveMaintenanceTires", label: "Troca de Pneus (Desgaste Natural)" },
        { id: "PreventiveMaintenanceBalancingAlignment", label: "Alinhamento e Balanceamento" },
        { id: "PreventiveMaintenanceBrakes", label: "Freios (Revisão Preventiva)" }
    ],
    "CorrectiveMaintenance": [
        { id: "CorrectiveMaintenanceMechanicalFailure", label: "Falha Mecânica Geral" },
        { id: "CorrectiveMaintenanceEngine", label: "Motor (Falha / Aquecimento)" },
        { id: "CorrectiveMaintenanceElectric", label: "Parte Elétrica / Iluminação" },
        { id: "CorrectiveMaintenanceSuspension", label: "Suspensão e Amortecedores" },
        { id: "CorrectiveMaintenanceBrakes", label: "Freios (Falha / Barulho)" },
        { id: "CorrectiveMaintenanceTransmission", label: "Câmbio / Embreagem" },
        { id: "CorrectiveMaintenanceBatteries", label: "Bateria" },
        { id: "CorrectiveMaintenanceAirConditioning", label: "Ar Condicionado" },
        { id: "CorrectiveMaintenanceBodyShop", label: "Funilaria / Lataria" },
        { id: "CorrectiveMaintenanceGlasses", label: "Vidros / Para-brisa" },
        { id: "CorrectiveMaintenanceTires", label: "Pneu Danificado (Furo / Rasgo)" },
        { id: "CorrectiveMaintenanceLeakage", label: "Vazamentos (Óleo / Água)" },
        { id: "CorrectiveMaintenanceAbnormalNoise", label: "Ruído Anormal / Barulho" },
        { id: "CorrectiveMaintenanceVehicleWontStart", label: "Veículo não liga / Não dá partida" },
        { id: "CorrectiveMaintenancePicks", label: "Retoques / Martelinho" }
    ],
    "Claim": [
        { id: "ClaimAccidentCollision", label: "Acidente / Colisão" },
        { id: "ClaimBodyAndPainting", label: "Funilaria e Pintura Completa" },
        { id: "ClaimGlasses", label: "Quebra de Vidros" },
        { id: "ClaimTheftOrRobberyVehicle", label: "Roubo ou Furto de Veículo" },
        { id: "ClaimTheftOrRobberyAccessories", label: "Furto de Rodas / Acessórios" },
        { id: "ClaimFlooding", label: "Alagamento / Enchente" }
    ]
};

function atualizarClasses(id, classePreSelecionada = null) {
    const selectTipo = document.getElementById('tipo_' + id);
    const selectClasse = document.getElementById('classe_' + id);
    if (!selectTipo || !selectClasse) return;
    const tipo = selectTipo.value;
    const classesDisponiveis = CLASSES_POR_TIPO[tipo] || [];

    selectClasse.innerHTML = '';
    classesDisponiveis.forEach(item => {
        const opt = document.createElement('option');
        opt.value = item.id;
        opt.textContent = item.label;
        if (classePreSelecionada && item.id === classePreSelecionada) {
            opt.selected = true;
        }
        selectClasse.appendChild(opt);
    });
}

let debounceTimerPlaca = {};

function validarTelefone(id) {
    const input = document.getElementById('tel_' + id);
    const status = document.getElementById('status_tel_' + id);
    if (!input || !status) return false;

    const digitos = input.value.replace(/\D/g, '');
    const qtd = digitos.length;

    if (qtd === 10 || qtd === 11) {
        input.style.borderColor = '#198754';
        input.style.backgroundColor = '#f0fff4';
        status.style.color = '#198754';
        status.textContent = `✓ ${qtd} dígitos (${qtd === 11 ? 'Celular com DDD' : 'Fixo com DDD'})`;
        return true;
    } else if (qtd === 0) {
        input.style.borderColor = '#dc3545';
        input.style.backgroundColor = '#fff5f5';
        status.style.color = '#dc3545';
        status.textContent = '⚠ Telefone obrigatório (10 ou 11 dígitos com DDD)';
        return false;
    } else if (qtd < 10) {
        input.style.borderColor = '#dc3545';
        input.style.backgroundColor = '#fff5f5';
        status.style.color = '#dc3545';
        status.textContent = `⚠ ${qtd} dígitos (Faltam ${10 - qtd} dígitos! Mínimo 10 com DDD)`;
        return false;
    } else {
        input.style.borderColor = '#dc3545';
        input.style.backgroundColor = '#fff5f5';
        status.style.color = '#dc3545';
        status.textContent = `⚠ ${qtd} dígitos (Excesso de ${qtd - 11} dígitos! Máximo 11 com DDD)`;
        return false;
    }
}

function validarPlacaEmTempoReal(id) {
    const input = document.getElementById('placa_' + id);
    const status = document.getElementById('status_placa_' + id);
    const headerPlaca = document.getElementById('header_placa_' + id);
    const headerModelo = document.getElementById('header_modelo_' + id);
    if (!input || !status) return;

    const placa = input.value.replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
    input.value = placa;
    if (headerPlaca) headerPlaca.textContent = placa || 'PLACA';

    if (placa.length < 7) {
        input.style.borderColor = '#dc3545';
        input.style.backgroundColor = '#fff5f5';
        status.style.color = '#dc3545';
        status.textContent = `⚠ Placa incompleta (${placa.length}/7)`;
        return;
    }

    status.style.color = '#6c757d';
    status.textContent = 'Verificando placa na frota...';

    clearTimeout(debounceTimerPlaca[id]);
    debounceTimerPlaca[id] = setTimeout(async () => {
        try {
            const res = await fetch(`/api/veiculo/${placa}/verificar`);
            let data;
            const resText = await res.text();
            try {
                data = JSON.parse(resText);
            } catch(jsonErr) {
                throw new Error(res.status === 500 || res.status === 504 ? "O servidor demorou para responder. Tente novamente." : "Falha na resposta do servidor (" + res.status + ")");
            }
            if (data.isSuccess && data.existe) {
                input.style.borderColor = '#198754';
                input.style.backgroundColor = '#f0fff4';
                status.style.color = '#198754';
                const desc = [data.montadora, data.modelo].filter(Boolean).join(' ');
                status.textContent = `✓ ${desc || 'Veículo localizado'}`;
                if (headerModelo) {
                    headerModelo.textContent = desc ? `• ${desc}` : '';
                }
                const badge = document.getElementById('badge_tipo_' + id);
                const boxCliente = document.getElementById('box_cliente_' + id);
                if (data.cliente) {
                    if (badge) {
                        badge.className = 'badge bg-light text-dark text-uppercase px-2 py-1';
                        badge.textContent = 'Alugado';
                    }
                    if (boxCliente) {
                        boxCliente.innerHTML = `
                            <div class="text-muted text-uppercase" style="font-size: 0.68rem; font-weight: 700; letter-spacing: 0.5px;">Cliente Vinculado</div>
                            <div class="fw-bold text-dark mt-1" style="font-size: 0.9rem;">
                                ${data.cliente} ${data.contrato_id ? '<span class="text-muted small fw-normal ms-1">(Contrato #' + data.contrato_id + ')</span>' : ''}
                            </div>
                        `;
                    }
                } else {
                    if (badge) {
                        badge.className = 'badge bg-success text-white text-uppercase px-2 py-1';
                        badge.textContent = 'Disponível';
                    }
                    if (boxCliente) {
                        boxCliente.innerHTML = `
                            <div class="text-muted text-uppercase" style="font-size: 0.68rem; font-weight: 700; letter-spacing: 0.5px;">Situação do Veículo</div>
                            <div class="fw-bold text-success mt-1" style="font-size: 0.9rem;">
                                Disponível
                            </div>
                        `;
                    }
                }
            } else {
                input.style.borderColor = '#dc3545';
                input.style.backgroundColor = '#fff5f5';
                status.style.color = '#dc3545';
                status.textContent = '⚠ Placa NÃO cadastrada na frota!';
                if (headerModelo) {
                    headerModelo.textContent = '• Não localizado';
                }
            }
        } catch (err) {
            status.style.color = '#dc3545';
            status.textContent = 'Erro ao verificar placa';
        }
    }, 300);
}

async function aprovarChecklist(id) {
    const telValido = validarTelefone(id);
    if (!telValido) {
        alert("Telefone inválido! Verifique a quantidade de dígitos (deve ter 10 ou 11 dígitos com DDD) antes de aprovar.");
        document.getElementById('tel_' + id)?.focus();
        return;
    }

    const placaVal = (document.getElementById('placa_' + id)?.value || '').replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
    if (placaVal.length !== 7) {
        alert("Placa inválida! Verifique os 7 dígitos da placa antes de aprovar.");
        document.getElementById('placa_' + id)?.focus();
        return;
    }

    if (!confirm("Confirmar envio para a Blue Fleet?")) return;

    const payload = {
        placa: document.getElementById('placa_' + id).value.trim(),
        km_atual: document.getElementById('km_' + id).value,
        descricao: document.getElementById('desc_' + id).value,
        data_agendamento: document.getElementById('data_' + id).value,
        motivo_id: document.getElementById('tipo_' + id).value,
        motivo_classe: document.getElementById('classe_' + id).value,
        solicitante: document.getElementById('solic_' + id).value.trim(),
        telefone: document.getElementById('tel_' + id).value.trim(),
        leva_e_traz: document.getElementById('leva_traz_' + id).value,
        sufixo_tipo: document.getElementById('tipo_pdf_' + id)?.value || 'es'
    };

    const btnAprovar = document.querySelector('#card_' + id + ' .btn-approve');
    if (btnAprovar) {
        btnAprovar.disabled = true;
        btnAprovar.innerText = "Enviando...";
    }

    try {
        const response = await fetch('/painel-nevada/aprovar/' + id, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        if (data.isSuccess) {
            let msg = "Sucesso! Ocorrência criada na Blue Fleet: ID " + data.contractItemRequestId;
            if (data.purchaseOrderNumber) {
                msg += "\nOrdem de Compra gerada: " + data.purchaseOrderNumber;
            }
            alert(msg);
            document.getElementById('card_' + id)?.remove();
        } else {
            alert("Erro da Blue Fleet: " + (data.erro || JSON.stringify(data)));
            if (btnAprovar) {
                btnAprovar.disabled = false;
                btnAprovar.innerText = "Aprovar e Enviar";
            }
        }
    } catch (error) {
        alert("Erro ao conectar com o servidor: " + error);
        if (btnAprovar) {
            btnAprovar.disabled = false;
            btnAprovar.innerText = "Aprovar e Enviar";
        }
    }
}

async function rejeitarChecklist(id) {
    if (!confirm("Tem certeza que deseja descartar este checklist?")) return;

    try {
        const response = await fetch('/painel-nevada/rejeitar/' + id, { method: 'POST' });
        const data = await response.json();
        if (data.isSuccess) {
            document.getElementById('card_' + id)?.remove();
        } else {
            alert("Erro ao descartar: " + data.erro);
        }
    } catch (error) {
        alert("Erro ao conectar com o servidor.");
    }
}

let debounceTimerModal = null;
function validarPlacaModal() {
    const input = document.getElementById('manual_placa');
    const status = document.getElementById('manual_status_placa');
    if (!input || !status) return;
    const placa = input.value.replace(/[^a-zA-Z0-9]/g, '').toUpperCase();
    input.value = placa;

    if (placa.length < 7) {
        input.style.borderColor = '#dc3545';
        status.style.color = '#dc3545';
        status.textContent = `⚠ ${placa.length}/7 caracteres`;
        return;
    }

    status.style.color = '#6c757d';
    status.textContent = 'Verificando...';

    clearTimeout(debounceTimerModal);
    debounceTimerModal = setTimeout(async () => {
        try {
            const res = await fetch(`/api/veiculo/${placa}/verificar`);
            let data;
            const resText = await res.text();
            try {
                data = JSON.parse(resText);
            } catch(jsonErr) {
                throw new Error(res.status === 500 || res.status === 504 ? "O servidor demorou para responder. Tente novamente." : "Falha na resposta do servidor (" + res.status + ")");
            }
            if (data.isSuccess && data.existe) {
                input.style.borderColor = '#198754';
                status.style.color = '#198754';
                const desc = [data.montadora, data.modelo].filter(Boolean).join(' ');
                status.textContent = `✓ ${desc || 'Veículo localizado'} ${data.cliente ? '(' + data.cliente + ')' : '(Disponível)'}`;
            } else {
                input.style.borderColor = '#dc3545';
                status.style.color = '#dc3545';
                status.textContent = '⚠ Placa não cadastrada na frota!';
            }
        } catch(e) {
            status.style.color = '#dc3545';
            status.textContent = 'Erro ao verificar placa';
        }
    }, 300);
}

function detectarPlacaNomeArquivo(input) {
    if (!input.files || input.files.length === 0) return;
    const file = input.files[0];
    const nome = file.name;

    // Se o arquivo já tiver o padrão PLACA-DATA-TIPO (ex: UEX2G82-22.10.26-es.pdf)
    const matchPlaca = nome.match(/^([A-Za-z0-9]{7})-/);
    if (matchPlaca) {
        const inputPlaca = document.getElementById('manual_placa');
        if (inputPlaca && !inputPlaca.value) {
            inputPlaca.value = matchPlaca[1].toUpperCase();
            validarPlacaModal();
        }
    }
}

async function enviarChecklistManual() {
    const fileInput = document.getElementById('manual_pdf');
    if (!fileInput.files || fileInput.files.length === 0) {
        alert("Por favor, selecione o arquivo PDF do checklist!");
        fileInput.focus();
        return;
    }

    const file = fileInput.files[0];
    const pecasFileInput = document.getElementById('manual_pdf_pecas');
    const placaInput = document.getElementById('manual_placa');
    const kmInput = document.getElementById('manual_km');
    const tipoInput = document.getElementById('manual_tipo');
    const solicInput = document.getElementById('manual_solic');
    const telInput = document.getElementById('manual_tel');
    const descInput = document.getElementById('manual_desc');

    const btn = document.getElementById('btn_salvar_manual');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Enviando...';

    const toBase64 = f => new Promise((resolve, reject) => {
        const r = new FileReader();
        r.readAsDataURL(f);
        r.onload = () => resolve(r.result.split(',')[1]);
        r.onerror = error => reject(error);
    });

    try {
        const base64Data = await toBase64(file);
        let pecasBase64 = null;
        if (pecasFileInput && pecasFileInput.files && pecasFileInput.files.length > 0) {
            pecasBase64 = await toBase64(pecasFileInput.files[0]);
        }

        const payload = {
            placa: placaInput.value.trim(),
            km_atual: kmInput.value || 0,
            motivo_id: tipoInput.value,
            solicitante: solicInput.value.trim(),
            telefone: telInput.value.trim(),
            descricao: descInput.value.trim(),
            pdf_base64: base64Data,
            pdf_nome: file.name,
            pecas_pdf_base64: pecasBase64
        };

        const res = await fetch('/painel-nevada/upload-imediato', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (data.isSuccess) {
            const modalEl = document.getElementById('modalUploadManual');
            const modalInstance = bootstrap.Modal.getInstance(modalEl);
            if (modalInstance) modalInstance.hide();
            window.location.reload();
        } else {
            alert("Erro ao adicionar checklist: " + (data.erro || JSON.stringify(data)));
            btn.disabled = false;
            btn.innerText = "Adicionar ao Painel";
        }
    } catch(err) {
        alert("Erro ao enviar: " + err);
        btn.disabled = false;
        btn.innerText = "Adicionar ao Painel";
    }
}

function iniciarMonitoramentoCard(id) {
    const interval = setInterval(async () => {
        try {
            const res = await fetch('/painel-nevada/status-card/' + id);
            const json = await res.json();
            if (json.isSuccess && json.status_ocr === 'concluido') {
                clearInterval(interval);
                aplicarDadosExtraidosNoCard(id, json.dados);
            }
        } catch(e) {
            console.error("Erro ao monitorar status do card:", e);
        }
    }, 2500);
}

function aplicarDadosExtraidosNoCard(id, dados) {
    const card = document.getElementById('card_' + id);
    if (!card) return;

    // 1. Atualiza o banner de OCR
    const banner = document.getElementById('banner_ocr_' + id);
    if (banner) {
        banner.className = 'alert alert-success py-2 px-3 mb-3 d-flex align-items-center gap-2';
        banner.innerHTML = '<strong>✓ IA concluiu a leitura da caligrafia!</strong>';
        setTimeout(() => { banner.remove(); }, 3500);
    }

    // 2. Preenche os campos do formulário
    if (dados.placa && dados.placa !== "NÃO IDENTIFICADA" && dados.placa !== "AGUARDANDO IA...") {
        const inputPlaca = document.getElementById('placa_' + id);
        if (inputPlaca) inputPlaca.value = dados.placa;
        const headerPlaca = document.getElementById('header_placa_' + id);
        if (headerPlaca) headerPlaca.textContent = dados.placa;
    }
    if (dados.km_atual) {
        const inputKm = document.getElementById('km_' + id);
        if (inputKm) inputKm.value = dados.km_atual;
    }
    if (dados.solicitante) {
        const inputSolic = document.getElementById('solic_' + id);
        if (inputSolic) inputSolic.value = dados.solicitante;
    }
    if (dados.telefone) {
        const inputTel = document.getElementById('tel_' + id);
        if (inputTel) inputTel.value = dados.telefone;
    }
    if (dados.descricao) {
        const inputDesc = document.getElementById('desc_' + id);
        if (inputDesc) inputDesc.value = dados.descricao;
    }
    if (dados.data_agendamento) {
        const inputData = document.getElementById('data_' + id);
        if (inputData) inputData.value = dados.data_agendamento.slice(0, 16);
    }
    if (dados.sufixo_tipo) {
        const selectTipoPdf = document.getElementById('tipo_pdf_' + id);
        if (selectTipoPdf) selectTipoPdf.value = dados.sufixo_tipo;
    }

    // 3. Atualiza cabeçalho e modelo
    const headerModelo = document.getElementById('header_modelo_' + id);
    if (headerModelo && (dados.montadora || dados.modelo_veiculo)) {
        headerModelo.textContent = `• ${dados.montadora} ${dados.modelo_veiculo}`;
    }

    // 4. Atualiza badge e box do cliente
    const badge = document.getElementById('badge_tipo_' + id);
    const boxCliente = document.getElementById('box_cliente_' + id);
    if (dados.cliente_nome) {
        if (badge) {
            badge.className = 'badge bg-light text-dark text-uppercase px-2 py-1';
            badge.textContent = 'Alugado';
        }
        if (boxCliente) {
            boxCliente.innerHTML = `
                <div class="text-muted text-uppercase" style="font-size: 0.68rem; font-weight: 700; letter-spacing: 0.5px;">Cliente Vinculado</div>
                <div class="fw-bold text-dark mt-1" style="font-size: 0.9rem;">
                    ${dados.cliente_nome} ${dados.contrato_id ? '<span class="text-muted small fw-normal ms-1">(Contrato #' + dados.contrato_id + ')</span>' : ''}
                </div>
            `;
        }
    } else {
        if (badge) {
            badge.className = 'badge bg-success text-white text-uppercase px-2 py-1';
            badge.textContent = 'Disponível';
        }
        if (boxCliente) {
            boxCliente.innerHTML = `
                <div class="text-muted text-uppercase" style="font-size: 0.68rem; font-weight: 700; letter-spacing: 0.5px;">Situação do Veículo</div>
                <div class="fw-bold text-success mt-1" style="font-size: 0.9rem;">
                    Disponível
                </div>
            `;
        }
    }

    // 5. Reativa o botão de aprovação
    const btnAprovar = document.getElementById('btn_aprovar_' + id);
    if (btnAprovar) {
        btnAprovar.disabled = false;
        btnAprovar.innerText = "Aprovar e Enviar";
    }

    // 6. Roda validações visuais em tempo real (borda verde/vermelha)
    validarTelefone(id);
    validarPlacaEmTempoReal(id);
}

// --- FUNÇÕES DA REQUISIÇÃO DE PEÇAS ---
function coletarItensPecasDoCard(id) {
    const lista = document.getElementById('lista_itens_pecas_' + id);
    if (!lista) return [];
    const linhas = lista.querySelectorAll('.row-peca');
    const itens = [];
    linhas.forEach(row => {
        const inputQtd = row.querySelector('.peca-qtd');
        const inputDesc = row.querySelector('.peca-desc');
        const inputPreco = row.querySelector('.peca-preco');
        if (!inputDesc) return;

        const qtdStr = (inputQtd ? inputQtd.value : '1').replace(',', '.').trim();
        const precoStr = (inputPreco ? inputPreco.value : '0').replace('R$', '').replace('$', '').replace(',', '.').trim();
        const descStr = inputDesc.value.trim().toUpperCase();

        const qtd = parseFloat(qtdStr) || 1.0;
        const unit = parseFloat(precoStr) || 0.0;

        if (descStr) {
            itens.push({
                quantidade: qtd,
                descricao: descStr,
                valor_unitario: unit
            });
        }
    });
    return itens;
}

function recalcularPecasCard(id) {
    const lista = document.getElementById('lista_itens_pecas_' + id);
    if (!lista) return;
    const badgeTotal = document.getElementById('badge_pecas_total_' + id);
    const linhas = lista.querySelectorAll('.row-peca');
    let totalGeral = 0.0;

    linhas.forEach(row => {
        const inputQtd = row.querySelector('.peca-qtd');
        const inputPreco = row.querySelector('.peca-preco');
        const spanSubtotal = row.querySelector('.peca-subtotal');

        const qtdStr = (inputQtd ? inputQtd.value : '1').replace(',', '.').trim();
        const precoStr = (inputPreco ? inputPreco.value : '0').replace('R$', '').replace('$', '').replace(',', '.').trim();

        const qtd = parseFloat(qtdStr) || 1.0;
        const unit = parseFloat(precoStr) || 0.0;
        const subtotal = Math.round(qtd * unit * 100) / 100;
        totalGeral += subtotal;

        if (spanSubtotal) {
            spanSubtotal.textContent = 'R$ ' + subtotal.toFixed(2).replace('.', ',');
        }
    });

    if (badgeTotal) {
        badgeTotal.textContent = 'R$ ' + totalGeral.toFixed(2).replace('.', ',');
        badgeTotal.classList.remove('d-none');
    }
}

function adicionarLinhaPeca(id) {
    const lista = document.getElementById('lista_itens_pecas_' + id);
    if (!lista) return;

    const div = document.createElement('div');
    div.className = 'row-peca d-flex align-items-center gap-1 mb-1';
    div.innerHTML = `
        <input type="text" class="form-control form-control-sm text-center fw-bold peca-qtd" style="width: 45px; font-size: 0.78rem; padding: 2px 4px;" value="1" oninput="recalcularPecasCard('${id}')">
        <input type="text" class="form-control form-control-sm peca-desc" style="font-size: 0.78rem; padding: 2px 6px;" placeholder="Nome da peça" oninput="recalcularPecasCard('${id}')">
        <input type="text" class="form-control form-control-sm text-end peca-preco" style="width: 65px; font-size: 0.78rem; padding: 2px 4px;" value="0.00" oninput="recalcularPecasCard('${id}')">
        <span class="peca-subtotal text-end fw-bold text-dark" style="width: 65px; font-size: 0.78rem;">R$ 0,00</span>
        <button type="button" class="btn btn-link text-danger p-0" style="width: 20px; text-decoration: none;" onclick="removerLinhaPeca('${id}', this)">✕</button>
    `;
    lista.appendChild(div);
    recalcularPecasCard(id);
}

function removerLinhaPeca(id, btn) {
    const row = btn.closest('.row-peca');
    if (row) {
        row.remove();
        recalcularPecasCard(id);
    }
}

async function subirPapelPecas(id) {
    const input = document.getElementById('input_pecas_' + id);
    if (!input.files || input.files.length === 0) return;

    const file = input.files[0];
    const statusEl = document.getElementById('status_pecas_' + id);
    const boxEl = document.getElementById('box_pecas_' + id);
    const badgeTotal = document.getElementById('badge_pecas_total_' + id);
    const listaItens = document.getElementById('lista_itens_pecas_' + id);
    const ocInput = document.getElementById('oc_input_' + id);

    if (statusEl) {
        statusEl.className = 'small text-primary mb-2 fw-semibold';
        statusEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> IA decifrando e calculando peças...';
    }

    const reader = new FileReader();
    reader.onload = async function() {
        try {
            const base64Data = reader.result;
            const res = await fetch('/painel-nevada/upload-pecas/' + id, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pdf_base64: base64Data })
            });

            let data;
            const resText = await res.text();
            try {
                data = JSON.parse(resText);
            } catch(jsonErr) {
                throw new Error(res.status === 500 || res.status === 504 ? "O servidor demorou para responder. Tente novamente." : "Falha na resposta do servidor (" + res.status + ")");
            }
            if (data.isSuccess && data.dados_pecas) {
                const pecas = data.dados_pecas;
                if (statusEl) statusEl.className = 'd-none';

                // Renderiza itens em tabela interativa editável
                if (listaItens && pecas.itens) {
                    listaItens.innerHTML = pecas.itens.map(p => `
                        <div class="row-peca d-flex align-items-center gap-1 mb-1">
                            <input type="text" class="form-control form-control-sm text-center fw-bold peca-qtd" style="width: 45px; font-size: 0.78rem; padding: 2px 4px;" value="${p.quantidade_formatada || p.quantidade}" oninput="recalcularPecasCard('${id}')">
                            <input type="text" class="form-control form-control-sm peca-desc" style="font-size: 0.78rem; padding: 2px 6px;" value="${p.descricao_pura || p.descricao}" oninput="recalcularPecasCard('${id}')">
                            <input type="text" class="form-control form-control-sm text-end peca-preco" style="width: 65px; font-size: 0.78rem; padding: 2px 4px;" value="${(p.valor_unitario || 0).toFixed(2)}" oninput="recalcularPecasCard('${id}')">
                            <span class="peca-subtotal text-end fw-bold text-dark" style="width: 65px; font-size: 0.78rem;">${p.subtotal_formatado || 'R$ 0,00'}</span>
                            <button type="button" class="btn btn-link text-danger p-0" style="width: 20px; text-decoration: none;" onclick="removerLinhaPeca('${id}', this)">✕</button>
                        </div>
                    `).join('');
                }

                // Atualiza badge de total
                if (badgeTotal && pecas.total_geral_formatado) {
                    badgeTotal.textContent = pecas.total_geral_formatado;
                    badgeTotal.className = 'badge bg-success';
                }

                // Preenche OC se a IA detectou
                if (ocInput && pecas.oc_num && !ocInput.value) {
                    ocInput.value = pecas.oc_num;
                }

                if (boxEl) boxEl.classList.remove('d-none');
            } else {
                if (statusEl) {
                    statusEl.className = 'small text-danger mb-2';
                    statusEl.textContent = 'Erro ao ler peças: ' + (data.erro || 'Falha na leitura');
                }
            }
        } catch(err) {
            if (statusEl) {
                statusEl.className = 'small text-danger mb-2';
                statusEl.textContent = 'Erro no envio: ' + err;
            }
        }
    };
    reader.readAsDataURL(file);
}

async function gerarPdfPecas(id) {
    const ocInput = document.getElementById('oc_input_' + id);
    const ocVal = ocInput ? ocInput.value.trim() : '';
    const btnVer = document.getElementById('btn_ver_pdf_pecas_' + id);
    const nomePdfEl = document.getElementById('nome_pdf_pecas_' + id);
    const itensPecas = coletarItensPecasDoCard(id);

    try {
        const res = await fetch('/painel-nevada/gerar-pdf-pecas/' + id, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ oc_num: ocVal, itens: itensPecas })
        });

        const data = await res.json();
        if (data.isSuccess) {
            if (btnVer) {
                btnVer.classList.remove('d-none');
                btnVer.href = data.url_download + '?t=' + new Date().getTime();
            }
            if (nomePdfEl && data.nome_arquivo) {
                nomePdfEl.textContent = data.nome_arquivo;
            }
            window.open(data.url_download, '_blank');
        } else {
            alert('Erro ao gerar PDF: ' + (data.erro || 'Desconhecido'));
        }
    } catch(e) {
        alert('Erro ao solicitar PDF: ' + e);
    }
}

// Inicializa cada card com sua respectiva classe inicial e validações
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.card-checklist[data-id]').forEach(function(card) {
        const id = card.getAttribute('data-id');
        const classe = card.getAttribute('data-classe');
        const statusOcr = card.getAttribute('data-status-ocr');

        atualizarClasses(id, classe);

        if (statusOcr === 'processando') {
            iniciarMonitoramentoCard(id);
        } else {
            validarTelefone(id);
            validarPlacaEmTempoReal(id);
        }
    });
});
