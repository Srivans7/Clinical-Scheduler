/* TrialScheduler - history UI */

(function () {
  "use strict";

  var searchInput = document.getElementById("history-search");
  var statusSelect = document.getElementById("history-status");
  var refreshButton = document.getElementById("history-refresh");
  var deleteAllButton = document.getElementById("history-delete-all");
  var historyCount = document.getElementById("history-count");
  var historyStats = document.getElementById("history-stats");
  var historyList = document.getElementById("history-list");
  var topToast = document.getElementById("top-toast");
  var toastTimer = null;

  initialize();

  function initialize() {
    searchInput.addEventListener("input", debounce(loadHistory, 220));
    statusSelect.addEventListener("change", loadHistory);
    refreshButton.addEventListener("click", loadHistory);
    if (deleteAllButton) {
      deleteAllButton.addEventListener("click", deleteAllTransactions);
    }
    historyList.addEventListener("click", onHistoryListClick);
    loadHistory();
  }

  async function onHistoryListClick(event) {
    var deleteButton = event.target.closest('[data-action="delete-transaction"]');
    if (!deleteButton) {
      return;
    }

    var transactionId = deleteButton.getAttribute("data-transaction-id");
    if (!transactionId) {
      return;
    }

    var confirmed = window.confirm("Delete transaction " + transactionId + " and its linked records?");
    if (!confirmed) {
      return;
    }

    deleteButton.disabled = true;
    var previousLabel = deleteButton.textContent;
    deleteButton.textContent = "Deleting...";

    try {
      await fetchJson("/api/audit-log/" + encodeURIComponent(transactionId), {
        method: "DELETE"
      });
      showTopToast("Deleted " + transactionId + " successfully.", "success");
      await loadHistory();
    } catch (error) {
      window.alert(error.message || "Failed to delete transaction.");
      deleteButton.disabled = false;
      deleteButton.textContent = previousLabel;
    }
  }

  async function loadHistory() {
    refreshButton.disabled = true;
    refreshButton.textContent = "Refreshing...";
    try {
      var query = new URLSearchParams({
        limit: "50",
        q: searchInput.value.trim(),
        status: statusSelect.value
      });
      var response = await fetchJson("/api/audit-log?" + query.toString());
      var items = response.items || [];
      updateDeleteAllButton(items.length);
      renderStats(items);
      historyCount.textContent = items.length + (items.length === 1 ? " record" : " records");
      await renderHistory(items);
    } catch (error) {
      historyList.innerHTML = '<div class="empty-state">' + escapeHtml(error.message || "Failed to load history.") + '</div>';
      historyStats.innerHTML = '';
      historyCount.textContent = '0 records';
      updateDeleteAllButton(0);
    } finally {
      refreshButton.disabled = false;
      refreshButton.textContent = "Refresh";
    }
  }

  async function deleteAllTransactions() {
    if (!deleteAllButton || deleteAllButton.disabled) {
      return;
    }
    var totalRecords = parseInt(historyCount.textContent, 10) || 0;
    if (totalRecords <= 0) {
      return;
    }

    var confirmed = window.confirm("Delete ALL transactions and linked records? This cannot be undone.");
    if (!confirmed) {
      return;
    }

    deleteAllButton.disabled = true;
    var previousLabel = deleteAllButton.textContent;
    deleteAllButton.textContent = "Deleting all...";

    try {
      var response = await fetchJson("/api/audit-log", { method: "DELETE" });
      showTopToast("Deleted " + String(response.transactions_deleted || 0) + " transactions successfully.", "success");
      await loadHistory();
    } catch (error) {
      window.alert(error.message || "Failed to delete all transactions.");
    } finally {
      deleteAllButton.disabled = false;
      deleteAllButton.textContent = previousLabel;
      updateDeleteAllButton(parseInt(historyCount.textContent, 10) || 0);
    }
  }

  function renderStats(items) {
    var scheduled = items.filter(function (item) { return item.status === "scheduled"; }).length;
    var uploaded = items.filter(function (item) { return item.status === "uploaded"; }).length;
    var withOutput = items.filter(function (item) { return !!item.latest_output_record_id; }).length;
    var uniqueFiles = Array.from(new Set(items.map(function (item) { return item.file_id; }))).length;

    historyStats.innerHTML = [
      statCard("Transactions", items.length),
      statCard("Scheduled", scheduled),
      statCard("Uploaded only", uploaded),
      statCard("Unique files", uniqueFiles || withOutput)
    ].join("");
  }

  async function renderHistory(items) {
    if (!items.length) {
      historyList.innerHTML = '<div class="empty-state">No transactions found for the current filters.</div>';
      return;
    }

    var detailPromises = items.map(function (item) {
      return fetchJson("/api/audit-log/" + encodeURIComponent(item.transaction_id)).catch(function () {
        return null;
      });
    });
    var details = await Promise.all(detailPromises);

    historyList.innerHTML = details.map(function (detail, index) {
      var item = items[index];
      if (!detail) {
        return fallbackCard(item, index + 1);
      }
      return detailCard(detail, index + 1);
    }).join("");
  }

  function detailCard(detail, rowNumber) {
    var latestOutput = detail.outputs.length ? detail.outputs[detail.outputs.length - 1] : null;
    var latestInput = detail.inputs.length ? detail.inputs[detail.inputs.length - 1] : null;

    return '<article class="history-card">' +
      '<div class="history-card-head">' +
        '<div class="history-title-wrap">' +
          '<span class="history-row-badge">Row ' + escapeHtml(rowNumber) + '</span>' +
          '<div class="history-card-title">' + escapeHtml(detail.transaction_id) + ' / ' + escapeHtml(detail.file_id) + '</div>' +
        '</div>' +
        '<div class="history-card-actions">' +
          '<div class="history-status ' + escapeHtml(detail.status) + '">' + escapeHtml(detail.status) + '</div>' +
          '<button type="button" class="btn btn-ghost btn-danger" data-action="delete-transaction" data-transaction-id="' + escapeHtml(detail.transaction_id) + '">Delete</button>' +
        '</div>' +
      '</div>' +
      '<div class="history-card-body">' +
        '<section class="history-section">' +
          '<div class="history-section-title">Record Summary</div>' +
          '<div class="history-grid">' +
            miniCard('Filename', detail.file ? detail.file.original_filename : '-') +
            miniCard('Latest Output', latestOutput ? latestOutput.output_record_id : 'Pending') +
            miniCard('Updated', detail.updated_at || '-') +
            miniCard('Recommended', latestOutput && latestOutput.results_summary ? latestOutput.results_summary.recommended_strategy : 'n/a') +
          '</div>' +
          '<div class="history-section-title">Latest Request Input</div>' +
          '<div class="history-grid">' + renderInputPayload(latestInput ? latestInput.payload : null) + '</div>' +
        '</section>' +
        '<section class="history-section">' +
          '<div class="history-section-title">Timeline</div>' +
          '<div class="timeline">' +
            timelineRow('Created', detail.created_at || '-') +
            timelineRow('Upload Op', findOperation(detail.operations, 'upload')) +
            timelineRow('Confirm Op', findOperation(detail.operations, 'schedule_confirm')) +
            timelineRow('Output', latestOutput ? latestOutput.output_record_id : 'Pending') +
            timelineRow('Storage', 'SQLite') +
          '</div>' +
        '</section>' +
      '</div>' +
    '</article>';
  }

  function fallbackCard(item, rowNumber) {
    return '<article class="history-card">' +
      '<div class="history-card-head">' +
        '<div class="history-title-wrap">' +
          '<span class="history-row-badge">Row ' + escapeHtml(rowNumber) + '</span>' +
          '<div class="history-card-title">' + escapeHtml(item.transaction_id) + ' / ' + escapeHtml(item.file_id) + '</div>' +
        '</div>' +
        '<div class="history-card-actions">' +
          '<div class="history-status ' + escapeHtml(item.status || '') + '">' + escapeHtml(item.status || 'unknown') + '</div>' +
          '<button type="button" class="btn btn-ghost btn-danger" data-action="delete-transaction" data-transaction-id="' + escapeHtml(item.transaction_id) + '">Delete</button>' +
        '</div>' +
      '</div>' +
      '<div class="history-card-body">' +
        '<section class="history-section">' +
          '<div class="history-grid">' +
            miniCard('Filename', item.filename || '-') +
            miniCard('Updated', item.updated_at || '-') +
            miniCard('Latest Output', item.latest_output_record_id || 'Pending') +
            miniCard('Recommended', item.recommended_strategy || 'n/a') +
          '</div>' +
        '</section>' +
      '</div>' +
    '</article>';
  }

  function renderInputPayload(payload) {
    if (!payload) {
      return miniCard('Input', 'No confirmed input yet');
    }
    return [
      miniCard('Protocol', payload.protocol),
      miniCard('Participants', String((payload.male || 0) + (payload.female || 0))),
      miniCard('Periods', payload.periods),
      miniCard('Check-in', payload.preferred_checkin)
    ].join('');
  }

  function findOperation(operations, type) {
    var op = (operations || []).find(function (item) { return item.type === type; });
    return op ? op.operation_id : 'n/a';
  }

  function statCard(label, value) {
    return '<div class="summary-card"><div class="summary-label">' + escapeHtml(label) + '</div><div class="summary-value">' + escapeHtml(value) + '</div></div>';
  }

  function miniCard(label, value) {
    return '<div class="mini-card"><div class="mini-label">' + escapeHtml(label) + '</div><div class="mini-value">' + escapeHtml(value) + '</div></div>';
  }

  function timelineRow(label, value) {
    return '<div class="timeline-row"><div class="timeline-label">' + escapeHtml(label) + '</div><div class="timeline-value">' + escapeHtml(value) + '</div></div>';
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, wait);
    };
  }

  function fetchJson(url, options) {
    return fetch(url, options || {}).then(function (response) {
      return response.text().then(function (text) {
        var data = {};
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (_error) {
            data = { error: "Request failed with non-JSON response." };
          }
        }
        if (!response.ok) {
          var message = (data && data.error) || response.statusText || 'Request failed.';
          if (message === "Request failed with non-JSON response.") {
            message = "Request failed (" + response.status + "). Please refresh or restart the server.";
          }
          throw new Error(message);
        }
        return data;
      });
    });
  }

  function updateDeleteAllButton(recordCount) {
    if (!deleteAllButton) {
      return;
    }
    deleteAllButton.disabled = !recordCount;
  }

  function showTopToast(message, type) {
    if (!topToast) {
      return;
    }
    if (toastTimer) {
      window.clearTimeout(toastTimer);
      toastTimer = null;
    }
    topToast.textContent = message;
    topToast.className = "top-toast " + (type || "success");
    topToast.classList.remove("hidden");
    toastTimer = window.setTimeout(function () {
      topToast.classList.add("hidden");
      toastTimer = null;
    }, 2500);
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
