/**
 * Snapdeal Virtual KAM — Live KAM Brain Diagnostics & Summary Card Panel (M6 + M7).
 */

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtInr(num) {
  if (typeof num !== "number" || Number.isNaN(num)) return String(num ?? "-");
  return "₹" + num.toLocaleString("en-IN");
}

export function createKamBrainController({
  rootEl,
  summaryEl,
  getAppState,
}) {
  const brain = {
    mode: "inbound",
    snapshot: "current",
    activeProvider: "-",
    demoData: null,
    diagnosedLive: false,
    actionPlanLive: false,
    toolLogs: [],
    failovers: [],
    commitments: [],
    priorCommitments: [],
    followupSchedule: null,
    summaryCard: null,
    lastLatency: null,
    ungroundedTotal: 0,
  };

  function resetForNewSession(sessionPayload) {
    const sess = sessionPayload?.session || {};
    brain.mode = sess.mode || "inbound";
    brain.snapshot = sess.snapshot || "current";
    brain.activeProvider = sess.active_provider || "-";
    brain.demoData = sessionPayload?.demo_data || brain.demoData;
    brain.diagnosedLive = false;
    brain.actionPlanLive = false;
    brain.toolLogs = [];
    brain.failovers = [];
    brain.commitments = Array.isArray(sess.commitments) ? [...sess.commitments] : [];
    brain.priorCommitments = Array.isArray(sess.prior_commitments) ? [...sess.prior_commitments] : [];
    brain.followupSchedule = sess.followup_schedule || null;
    brain.summaryCard = null;
    brain.lastLatency = null;
    brain.ungroundedTotal = 0;
    render();
    renderSummaryCard();
  }

  function applyToolResult(name, result, latencyMs, sourceHint) {
    if (!result || typeof result !== "object") return;
    if (!brain.demoData) brain.demoData = {};

    if (name === "get_metric_comparison") {
      brain.demoData.metrics = result;
    } else if (name === "get_seller_overview") {
      brain.demoData.overview = result;
    } else if (name === "run_diagnosis") {
      brain.demoData.diagnosis = result;
      brain.diagnosedLive = true;
    } else if (name === "get_out_of_stock_skus") {
      brain.demoData.oos_skus = result;
    } else if (name === "get_cancellation_breakdown") {
      brain.demoData.cancellations = result;
    } else if (name === "get_pricing_gaps") {
      brain.demoData.pricing = result;
    } else if (name === "get_visibility_funnel") {
      brain.demoData.funnel = result;
    } else if (name === "build_action_plan") {
      brain.demoData.action_plan = result;
      brain.actionPlanLive = true;
    } else if (name === "commit_action" && result.commitment) {
      const idx = brain.commitments.findIndex(
        (c) => c.action_id === result.commitment.action_id
      );
      if (idx >= 0) brain.commitments[idx] = result.commitment;
      else brain.commitments.push(result.commitment);
    } else if (name === "schedule_followup" && result.scheduled) {
      brain.followupSchedule = result;
    } else if (name === "send_call_summary" && result.summary_card) {
      brain.summaryCard = result.summary_card;
    }

    // Attach latency/summary to matching running entry or push new
    let summary = "Completed";
    if (name === "run_diagnosis") {
      summary = `Attributed -${result.total_lost_orders} orders (${fmtInr(result.total_lost_revenue)})`;
    } else if (name === "get_out_of_stock_skus") {
      summary = `${result.total_oos_count} OOS SKUs (${result.newly_oos_count} newly OOS)`;
    } else if (name === "get_pricing_gaps") {
      summary = `${result.overpriced_sku_count} overpriced SKUs (-${result.total_pricing_lost_orders} orders)`;
    } else if (name === "get_cancellation_breakdown") {
      summary = `Cancel rate ${result.baseline_cancellation_rate_pct}% -> ${result.current_cancellation_rate_pct}%`;
    } else if (name === "get_visibility_funnel") {
      summary = `Impressions ${result.changes?.impressions_pct}%`;
    } else if (name === "build_action_plan") {
      summary = `${(result.actions || []).length} prioritised actions (${result.estimated_combined_recovery_rs || ""})`;
    } else if (name === "commit_action" && result.commitment) {
      summary = `Committed ${result.commitment.action_id} due ${result.commitment.due_date}`;
    } else if (name === "schedule_followup") {
      summary = `Scheduled: ${result.followup_date}`;
    } else if (name === "send_call_summary") {
      summary = "Generated Call Summary & WhatsApp text";
    } else if (name === "get_metric_comparison") {
      const drop = result.metrics?.sales_gmv?.delta_pct;
      summary = `Sales ${drop}% | ASP flat ₹${result.metrics?.asp?.current}`;
    } else if (name === "get_seller_overview") {
      summary = `Loaded ${result.seller?.shop_name || "Seller"} (${result.snapshot})`;
    }

    // Update latest pending log entry for this tool if any
    const pendingIdx = [...brain.toolLogs]
      .reverse()
      .findIndex((t) => t.name === name && t.status === "running");
    if (pendingIdx >= 0) {
      const realIdx = brain.toolLogs.length - 1 - pendingIdx;
      brain.toolLogs[realIdx] = {
        ...brain.toolLogs[realIdx],
        status: "done",
        latency_ms: latencyMs ?? 0,
        summary,
      };
    } else {
      brain.toolLogs.push({
        name,
        source: sourceHint || "tool",
        status: "done",
        latency_ms: latencyMs ?? 0,
        summary,
      });
    }
    render();
  }

  function renderMetricsSection() {
    const metricsPayload = brain.demoData?.metrics;
    const m = metricsPayload?.metrics;
    if (!m) return `<div class="kb-bento-card kb-empty">Loading account snapshot...</div>`;

    const isFollowup = brain.mode === "followup" || Boolean(metricsPayload.followup);
    const fu = metricsPayload.followup;

    const rows = [
      {
        label: "Sales (GMV)",
        base: fmtInr(m.sales_gmv.baseline),
        curr: fmtInr(m.sales_gmv.current),
        delta: `${m.sales_gmv.delta_pct}%`,
        deltaClass: "bad",
        fuVal: fu ? `${fmtInr(fu.sales_gmv_pace)} (+${fu.sales_vs_crisis_pct}%)` : "",
      },
      {
        label: "Orders Placed",
        base: String(m.orders.baseline),
        curr: String(m.orders.current),
        delta: `${m.orders.delta} (${m.orders.delta_pct}%)`,
        deltaClass: "bad",
        fuVal: fu ? `${fu.orders_pace} (+${fu.orders_vs_crisis_pct}%)` : "",
      },
      {
        label: "ASP (Avg Price)",
        base: fmtInr(m.asp.baseline),
        curr: fmtInr(m.asp.current),
        delta: "0.0% (Flat)",
        deltaClass: "neutral",
        fuVal: fu ? fmtInr(fu.asp) : "",
      },
      {
        label: "Out-of-Stock SKUs",
        base: String(m.out_of_stock_skus.baseline),
        curr: String(m.out_of_stock_skus.current),
        delta: `+${m.out_of_stock_skus.newly_oos} new`,
        deltaClass: "bad",
        fuVal: fu ? `${fu.out_of_stock_skus} (Target <= ${fu.oos_target_max})` : "",
      },
      {
        label: "Cancellation Rate",
        base: `${m.cancellation_rate_pct.baseline}%`,
        curr: `${m.cancellation_rate_pct.current}%`,
        delta: `+${m.cancellation_rate_pct.delta_pp} pp`,
        deltaClass: "bad",
        fuVal: fu ? `${fu.cancellation_rate_pct}% (Target <= ${fu.cancellation_target_max}%)` : "",
      },
    ];

    return `
      <div class="kb-block kb-bento-card">
        <div class="kb-block-head">
          <span>Store Performance Comparison</span>
          <span class="kb-tag ${isFollowup ? "good kb-pill-sage" : "kb-pill-coral"}">
            ${isFollowup ? "3-Day Recovery Active" : "Volume Problem · Flat ASP"}
          </span>
        </div>
        <div class="kb-table-wrap">
          <table class="kb-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Last Month</th>
                <th>This Month</th>
                <th>Change</th>
                ${isFollowup && fu ? "<th>3-Day Pace</th>" : ""}
              </tr>
            </thead>
            <tbody>
              ${rows
                .map(
                  (r) => `
                <tr>
                  <td>${esc(r.label)}</td>
                  <td>${esc(r.base)}</td>
                  <td>${esc(r.curr)}</td>
                  <td><span class="kb-delta ${esc(r.deltaClass)}">${esc(r.delta)}</span></td>
                  ${
                    isFollowup && fu
                      ? `<td><span class="kb-delta good">${esc(r.fuVal)}</span></td>`
                      : ""
                  }
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  function renderPriorCommitments() {
    if (brain.mode !== "followup" || !brain.priorCommitments.length) return "";
    return `
      <div class="kb-block kb-bento-card kb-followup-box">
        <div class="kb-block-head">
          <span>Prior Commitments Reviewed (3 Days Ago)</span>
          <span class="kb-tag good kb-pill-sage">Follow-Up Accountability</span>
        </div>
        <div class="kb-prior-list">
          ${brain.priorCommitments
            .map((c) => {
              const statusRaw = String(c.status || "pending");
              const isDone = statusRaw.includes("done") && !statusRaw.includes("partial");
              const badgeCls = isDone ? "good kb-pill-sage" : "warn kb-pill-amber";
              const label = c.summary || c.action_summary || c.action_id;
              return `
                <div class="kb-prior-item">
                  <div class="kb-prior-top">
                    <strong>${esc(c.action_id || "ACT")}: ${esc(label)}</strong>
                    <span class="kb-tag ${badgeCls}">${esc(statusRaw.replaceAll("_", " "))}</span>
                  </div>
                  ${c.detail ? `<div class="kb-sub">${esc(c.detail)}</div>` : ""}
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    `;
  }

  function renderDriversSection() {
    const diag = brain.demoData?.diagnosis;
    const oos = brain.demoData?.oos_skus;
    const pricing = brain.demoData?.pricing;
    if (!diag || !Array.isArray(diag.drivers)) return "";

    const colors = {
      stock_outs: "linear-gradient(90deg, #ff6347 0%, #fb7185 100%)",
      pricing_gaps: "linear-gradient(90deg, #f59e42 0%, #fbbf24 100%)",
      cancellations_visibility: "linear-gradient(90deg, #60a5fa 0%, #38bdf8 100%)",
    };

    const topOos = (oos?.top_oos_skus || []).slice(0, 3);
    const allPriceSkus = pricing?.pricing_gaps || [];
    const remSkuId =
      typeof pricing?.remaining_overpriced_sku === "string"
        ? pricing.remaining_overpriced_sku
        : pricing?.remaining_overpriced_sku?.sku_id;
    const priceSkus =
      brain.mode === "followup" && remSkuId
        ? allPriceSkus.filter((g) => g.sku_id === remSkuId || String(remSkuId).includes(g.sku_id))
        : allPriceSkus.slice(0, 3);

    return `
      <div class="kb-block kb-bento-card">
        <div class="kb-block-head">
          <span>Root-Cause Revenue Attribution (-${esc(diag.total_lost_orders)} Orders · ${esc(fmtInr(diag.total_lost_revenue))})</span>
          <span class="kb-tag ${brain.diagnosedLive ? "good kb-pill-sage" : ""}">
            ${brain.diagnosedLive ? "Live Diagnosis Verified" : "Snapshot Model"}
          </span>
        </div>
        <div class="kb-drivers">
          ${diag.drivers
            .map((d) => {
              const col = colors[d.driver] || "linear-gradient(90deg, #ff6347 0%, #f59e42 100%)";
              return `
                <div class="kb-driver-row">
                  <div class="kb-driver-meta">
                    <span>#${esc(d.rank)} ${esc(d.title)}</span>
                    <strong>${esc(d.share_pct)}% · ${esc(d.lost_orders)} orders (${esc(fmtInr(d.lost_revenue))})</strong>
                  </div>
                  <div class="kb-bar-track kb-driver-track">
                    <div class="kb-bar-fill kb-driver-fill" style="width: ${Math.min(100, Number(d.share_pct) || 0)}%; background: ${col};"></div>
                  </div>
                  <div class="kb-sub">${esc(d.evidence)}</div>
                </div>
              `;
            })
            .join("")}
        </div>
        ${
          topOos.length || priceSkus.length
            ? `
          <div class="kb-sku-grid">
            ${
              topOos.length
                ? `
              <div class="kb-mini-card">
                <div class="kb-mini-title">Top Out-of-Stock SKUs (Rev/Day Lost)</div>
                ${topOos
                  .map(
                    (s) =>
                      `<div class="kb-mini-row"><span>${esc(s.sku_id)} ${esc(s.name)}</span><strong>${esc(fmtInr(s.estimated_lost_rev_per_day))}/d</strong></div>`
                  )
                  .join("")}
              </div>`
                : ""
            }
            ${
              priceSkus.length
                ? `
              <div class="kb-mini-card">
                <div class="kb-mini-title">Price Gap SKUs (You vs Lowest)</div>
                ${priceSkus
                  .map(
                    (g) =>
                      `<div class="kb-mini-row"><span>${esc(g.sku_id)} ${esc(g.name)}</span><strong>${esc(fmtInr(g.seller_price))} vs ${esc(fmtInr(g.competitor_lowest_price))} (+${esc(g.price_gap_pct)}%)</strong></div>`
                  )
                  .join("")}
              </div>`
                : ""
            }
          </div>`
            : ""
        }
      </div>
    `;
  }

  function renderActionPlanAndCommitments() {
    const plan = brain.demoData?.action_plan;
    const actions = plan?.actions || [];
    if (!actions.length && !brain.commitments.length) return "";

    return `
      <div class="kb-block kb-bento-card">
        <div class="kb-block-head">
          <span>Prioritised Recovery Plan &amp; Seller Commitments</span>
          <span class="kb-tag ${brain.commitments.length ? "good kb-pill-sage" : "kb-pill-amber"}">
            ${brain.commitments.length} Committed
          </span>
        </div>
        <div class="kb-actions">
          ${actions
            .map((a) => {
              const matchedCommit = brain.commitments.find((c) => c.action_id === a.action_id);
              const priorityPill = a.priority === 1 ? "kb-pill-coral" : a.priority === 2 ? "kb-pill-amber" : "";
              return `
                <div class="kb-action-card ${matchedCommit ? "committed" : ""}">
                  <div class="kb-action-top">
                    <span class="kb-priority">P${esc(a.priority)} · ${esc(a.action_id)}</span>
                    <span class="kb-tag ${priorityPill}">${esc(a.impact)}</span>
                    ${
                      matchedCommit
                        ? `<span class="kb-tag good kb-pill-sage">Committed · Due: ${esc(matchedCommit.due_date)}</span>`
                        : `<span class="kb-sub">Suggested: ${esc(a.recommended_due)}</span>`
                    }
                  </div>
                  <div class="kb-action-title">${esc(a.title)}</div>
                  <div class="kb-sub">${esc(a.details)}</div>
                  <div class="kb-action-rec">Indicative Recovery: <strong>${esc(a.estimated_recovery_range_rs)}</strong></div>
                </div>
              `;
            })
            .join("")}
        </div>
        ${
          brain.commitments.filter((c) => !actions.some((a) => a.action_id === c.action_id)).length
            ? `
          <div class="kb-extra-commits">
            ${brain.commitments
              .filter((c) => !actions.some((a) => a.action_id === c.action_id))
              .map(
                (c) => `
                <div class="kb-prior-item">
                  <div class="kb-prior-top">
                    <strong>${esc(c.action_id)}: ${esc(c.action_summary)}</strong>
                    <span class="kb-tag good kb-pill-sage">${esc(c.owner)} · Due ${esc(c.due_date)}</span>
                  </div>
                </div>`
              )
              .join("")}
          </div>`
            : ""
        }
        ${
          brain.followupSchedule
            ? `
          <div class="kb-followup-pill">
            <strong>Next Follow-Up Check-In:</strong> ${esc(brain.followupSchedule.followup_date)}
            &nbsp;|&nbsp; Targets: ${esc((brain.followupSchedule.success_targets || []).join("; "))}
          </div>`
            : ""
        }
      </div>
    `;
  }

  function renderToolLogSection() {
    return `
      <div class="kb-block kb-bento-card">
        <div class="kb-block-head">
          <span>Live Agent Tool &amp; Failover Audit</span>
          <span class="kb-tag ${brain.ungroundedTotal === 0 ? "good kb-pill-sage" : "warn kb-pill-amber"}">
            ${
              brain.ungroundedTotal === 0
                ? "Grounding Check: 0 Ungrounded Numbers"
                : `Warning: ${brain.ungroundedTotal} Ungrounded`
            }
          </span>
        </div>
        ${
          brain.failovers.length
            ? `
          <div class="kb-failover-banner">
            Failover triggered: ${brain.failovers
              .map((f) => `${esc(f.from)} -> ${esc(f.to)} (${esc(f.reason)})`)
              .join(" | ")}
          </div>`
            : ""
        }
        <div class="kb-tool-log">
          ${
            brain.toolLogs.length
              ? [...brain.toolLogs]
                  .reverse()
                  .slice(0, 12)
                  .map(
                    (t) => `
                  <div class="kb-tool-row kb-audit-row">
                    <span class="kb-tool-name">${esc(t.name)}</span>
                    <span class="kb-tool-src">${esc(t.source)}</span>
                    <span class="kb-tool-ms">${
                      t.status === "running" ? "running..." : `${esc(t.latency_ms)} ms`
                    }</span>
                    <span class="kb-tool-summary">${esc(t.summary)}</span>
                  </div>`
                  )
                  .join("")
              : `<div class="kb-sub">Tool calls from the Deterministic Diagnosis Engine will stream here live during the call.</div>`
          }
        </div>
      </div>
    `;
  }

  function render() {
    if (!rootEl) return;
    const modeLabel =
      brain.mode === "followup"
        ? "3-Day Follow-Up"
        : brain.mode === "proactive"
        ? "Proactive Call (Riya)"
        : "Inbound Call";

    const modeBadgeClass =
      brain.mode === "followup"
        ? "good kb-pill-sage"
        : brain.mode === "proactive"
        ? "kb-pill-amber"
        : "kb-pill-coral";

    rootEl.innerHTML = `
      <div class="kb-status-strip">
        <span class="kb-tag ${modeBadgeClass}">${esc(modeLabel)}</span>
        <span class="kb-tag pill-mono">LLM: ${esc(brain.activeProvider || "-")}</span>
        ${
          brain.lastLatency
            ? `<span class="kb-tag pill-mono">First Token: ${esc(brain.lastLatency.llm_first_token_ms)} ms · Req/Turn: ${esc(brain.lastLatency.llm_requests)}</span>`
            : ""
        }
      </div>
      ${renderPriorCommitments()}
      ${renderMetricsSection()}
      ${renderDriversSection()}
      ${renderActionPlanAndCommitments()}
      ${renderToolLogSection()}
    `;
  }

  function renderSummaryCard() {
    if (!summaryEl) return;
    const card = brain.summaryCard;
    if (!card) {
      summaryEl.classList.add("hidden");
      summaryEl.innerHTML = "";
      return;
    }

    summaryEl.classList.remove("hidden");
    const commits = card.commitments || [];
    const fu = card.followup || {};
    summaryEl.innerHTML = `
      <div class="summary-card kb-bento-card">
        <div class="kb-block-head">
          <span>Call Summary &amp; Mock WhatsApp Recap</span>
          <div style="display:flex; gap:8px;">
            <button id="btnCopyWhatsapp" class="btn-pill btn-glass-sm" type="button">Copy WhatsApp Text</button>
            <button id="btnDownloadSummary" class="btn-pill btn-coral-sm" type="button">Download .txt</button>
          </div>
        </div>
        <div class="summary-grid">
          <div>
            <div class="kb-mini-title">Key Issues Covered</div>
            <ul class="summary-list">
              ${(card.key_issues || []).map((i) => `<li>${esc(i)}</li>`).join("")}
            </ul>
            <div class="kb-mini-title" style="margin-top:10px;">Committed Actions (${commits.length})</div>
            ${
              commits.length
                ? `<ul class="summary-list">
                    ${commits
                      .map(
                        (c) =>
                          `<li><strong>${esc(c.action_id)}</strong>: ${esc(c.action_summary)} (${esc(c.owner)} · Due: ${esc(c.due_date)})</li>`
                      )
                      .join("")}
                   </ul>`
                : `<div class="kb-sub">Standard recovery action targets included in WhatsApp message.</div>`
            }
            <div class="kb-followup-pill" style="margin-top:8px;">
              <strong>Follow-Up:</strong> ${esc(fu.followup_date || "In 3 days")}
              &nbsp;|&nbsp; ${(fu.success_targets || []).map((t) => esc(t)).join("; ")}
            </div>
          </div>
          <div>
            <div class="kb-mini-title">Mock WhatsApp / SMS Dispatch</div>
            <pre id="whatsappPreview" class="whatsapp-pre">${esc(card.whatsapp_text || "")}</pre>
          </div>
        </div>
      </div>
    `;

    const btnCopy = document.getElementById("btnCopyWhatsapp");
    if (btnCopy) {
      btnCopy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(card.whatsapp_text || "");
          btnCopy.textContent = "Copied!";
          setTimeout(() => {
            btnCopy.textContent = "Copy WhatsApp Text";
          }, 1800);
        } catch {
          btnCopy.textContent = "Copy Failed";
        }
      });
    }

    const btnDownload = document.getElementById("btnDownloadSummary");
    if (btnDownload) {
      btnDownload.addEventListener("click", () => {
        const blob = new Blob([card.whatsapp_text || ""], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "snapdeal-kam-call-summary.txt";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      });
    }
  }

  return {
    onDemoData(data) {
      brain.demoData = data;
      const appSt = getAppState ? getAppState() : null;
      if (appSt?.mode) brain.mode = appSt.mode;
      render();
    },
    onSessionStarted(payload) {
      resetForNewSession(payload);
    },
    onPhase(data) {
      if (data?.mode) brain.mode = data.mode;
      render();
    },
    onToolCall(data) {
      brain.toolLogs.push({
        name: data.name,
        source: data.source || "llm",
        status: "running",
        latency_ms: 0,
        summary: "Executing...",
      });
      render();
    },
    onToolResult(data) {
      applyToolResult(data.name, data.result, data.latency_ms, null);
    },
    onFailover(data) {
      brain.failovers.push(data);
      render();
    },
    onLatency(data) {
      brain.lastLatency = data;
      if (data?.provider) brain.activeProvider = data.provider;
      if (Array.isArray(data?.ungrounded_numbers) && data.ungrounded_numbers.length) {
        brain.ungroundedTotal += data.ungrounded_numbers.length;
      }
      render();
    },
    onSummaryCard(card) {
      brain.summaryCard = card;
      renderSummaryCard();
    },
    onDone(data) {
      if (data?.provider) brain.activeProvider = data.provider;
      if (Array.isArray(data?.commitments) && data.commitments.length) {
        brain.commitments = data.commitments;
      }
      if (data?.followup_schedule) {
        brain.followupSchedule = data.followup_schedule;
      }
      if (data?.summary_card && !brain.summaryCard) {
        brain.summaryCard = data.summary_card;
        renderSummaryCard();
      }
      render();
    },
  };
}
