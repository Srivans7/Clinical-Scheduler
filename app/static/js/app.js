/* TrialScheduler - planner UI */

(function () {
  "use strict";

  var dropZone = document.getElementById("drop-zone");
  var fileInput = document.getElementById("file-input");
  var btnUpload = document.getElementById("btn-upload");
  var btnResetUpload = document.getElementById("btn-reset-upload");
  var uploadStatus = document.getElementById("upload-status");
  var btnPreview = document.getElementById("btn-schedule");
  var btnConfirm = document.getElementById("btn-confirm");
  var btnRerun = document.getElementById("btn-rerun");
  var previewStatus = document.getElementById("preview-status");
  var previewBanner = document.getElementById("preview-banner");
  var sectionData = document.getElementById("section-data");
  var sectionResults = document.getElementById("section-results");
  var summaryStrip = document.getElementById("summary-strip");
  var clinicGrid = document.getElementById("clinic-grid");
  var existingTbody = document.querySelector("#tbl-existing tbody");
  var existingCount = document.getElementById("existing-count");
  var validationSummary = document.getElementById("validation-summary");
  var reviewMeta = document.getElementById("review-meta");
  var changePreview = document.getElementById("change-preview");
  var changeCount = document.getElementById("change-count");
  var strategyCards = document.getElementById("strategy-cards");
  var finalSummary = document.getElementById("final-summary");
  var auditSummary = document.getElementById("audit-summary");
  var auditHistory = document.getElementById("audit-history");
  var checkinAvailabilityEl = null;

  var nsFields = {
    protocol: document.getElementById("ns-protocol"),
    male: document.getElementById("ns-male"),
    female: document.getElementById("ns-female"),
    periods: document.getElementById("ns-periods"),
    washout: document.getElementById("ns-washout"),
    los: document.getElementById("ns-los"),
    checkin: document.getElementById("ns-checkin")
  };

  var step1El = document.getElementById("step-1");
  var step2El = document.getElementById("step-2");
  var step3El = document.getElementById("step-3");

  var fieldConfig = [
    { key: "protocol", label: "Protocol" },
    { key: "preferred_checkin", label: "Preferred check-in" },
    { key: "male", label: "Male participants" },
    { key: "female", label: "Female participants" },
    { key: "periods", label: "Periods" },
    { key: "washout", label: "Washout days" },
    { key: "los", label: "Length of stay" }
  ];

  var strategyMeta = {
    Shift: { icon: "S1", desc: "Shift dates while keeping participants together" },
    Split: { icon: "S2", desc: "Keep dates fixed and split across clinics" },
    "Shift+Split": { icon: "S3", desc: "Shift dates and split across clinics if needed" },
    "Alternative Shift Dates": { icon: "S4", desc: "Check-in date alternatives for simple shift (optional reference)" }
  };

  var MAX_SHIFT_DAYS = 120;  // Reference for alternative dates display

  var STORAGE_KEY = "trialScheduler.plannerState.v1";

  var state = {
    pendingFile: null,
    uploadedFilename: null,
    originalStudy: null,
    audit: null,
    clinics: [],
    existingSchedule: [],
    previewInput: null,
    previewSummary: null,
    previewResults: null,
    previewDirty: false,
    confirmedAudit: null,
    confirmedSummary: null
  };

  initialize();

  function initialize() {
    bindDropzone();
    bindForm();
    bindActions();
    if (isReloadNavigation()) {
      clearPersistedState();
      resetPreviewState();
    } else if (!restorePersistedState()) {
      resetPreviewState();
    }
    restorePlannerFromState();
    renderValidation([], [], true);
    renderReviewMeta();
    renderChangePreview();
    if (!state.previewResults) {
      renderResults([]);
    }
    renderFinalSummary();
    renderAuditSummary();
    renderAuditHistory([]);
    refreshReviewState();
    loadAuditHistory();
  }

  function bindDropzone() {
    // NOTE: dropZone is a <label> that wraps <input type="file">, so the browser
    // natively opens the file dialog on click. Do NOT also call fileInput.click()
    // here — that would open the dialog a second time and reset the selection.

    ["dragenter", "dragover"].forEach(function (evt) {
      dropZone.addEventListener(evt, function (event) {
        event.preventDefault();
        dropZone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach(function (evt) {
      dropZone.addEventListener(evt, function () {
        dropZone.classList.remove("dragover");
      });
    });
    

    dropZone.addEventListener("drop", function (event) {
      event.preventDefault();
      if (event.dataTransfer.files[0]) {
        attachFile(event.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener("change", function () {
      if (fileInput.files[0]) {
        attachFile(fileInput.files[0]);
      }
    });
  }

  function bindForm() {
    Object.keys(nsFields).forEach(function (key) {
      nsFields[key].addEventListener("input", onFormChanged);
      nsFields[key].addEventListener("change", onFormChanged);
    });
  }

  function ensureCheckinAvailabilityElement() {
    if (checkinAvailabilityEl) {
      return;
    }
    var checkinField = nsFields.checkin ? nsFields.checkin.closest(".form-field") : null;
    if (!checkinField) {
      return;
    }
    checkinAvailabilityEl = document.getElementById("checkin-availability");
    if (!checkinAvailabilityEl) {
      checkinAvailabilityEl = document.createElement("div");
      checkinAvailabilityEl.id = "checkin-availability";
      checkinAvailabilityEl.className = "checkin-availability";
      checkinField.appendChild(checkinAvailabilityEl);
    }
  }

  function bindActions() {
    btnUpload.addEventListener("click", uploadFile);
    btnResetUpload.addEventListener("click", resetUploadContext);
    btnPreview.addEventListener("click", previewSchedule);
    btnConfirm.addEventListener("click", confirmSchedule);
    btnRerun.addEventListener("click", function () {
      sectionResults.classList.add("hidden");
      sectionResults.classList.remove("fade-up");
      previewSchedule();
    });
  }

  function attachFile(file) {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      state.pendingFile = null;
      btnUpload.disabled = true;
      setStatus(uploadStatus, "Only .xlsx files are supported.", "error");
      return;
    }

    state.pendingFile = file;
    dropZone.classList.add("has-file");
    dropZone.querySelector(".dropzone-main").textContent = file.name;
    dropZone.querySelector(".dropzone-hint").textContent = formatFileSize(file.size) + " - ready for analysis";
    btnUpload.disabled = false;
    setStatus(uploadStatus, "", "");
  }

  async function uploadFile() {
    if (!state.pendingFile) {
      setStatus(uploadStatus, "Choose an Excel file before upload.", "error");
      return;
    }

    setStatus(uploadStatus, "Uploading and parsing file...", "loading");
    btnUpload.disabled = true;
    btnUpload.innerHTML = '<span class="spinner"></span> Analysing...';

    var formData = new FormData();
    formData.append("file", state.pendingFile);

    try {
      var response = await fetchJsonWithRetry("/api/upload", {
        method: "POST",
        body: formData
      }, 2, 350);

      state.uploadedFilename = response.filename;
      state.originalStudy = cloneValue(response.new_study || {});
      state.audit = response.audit || null;
      state.clinics = response.clinics || [];
      state.existingSchedule = response.existing_schedule || [];
      resetPreviewState();
      persistState();
      btnResetUpload.disabled = false;

      fillStudyForm(response.new_study || {});
      renderSummaryStrip(response);
      renderClinicGrid(state.clinics);
      renderExistingTable(state.existingSchedule);
      renderReviewMeta();
      refreshReviewState();
      renderFinalSummary();
      renderAuditSummary();
      sectionData.classList.remove("hidden");
      requestAnimationFrame(function () {
        sectionData.classList.add("fade-up");
      });
      markStepDone(step1El);
      markStepActive(step2El);
      var uploadWarnings = Array.isArray(response.warnings) ? response.warnings : [];
      if (uploadWarnings.length > 0) {
        setStatus(uploadStatus, "File parsed with warnings — see below.", "error");
        renderValidation([], uploadWarnings, false);
      } else {
        setStatus(uploadStatus, "Parsed " + state.existingSchedule.length + " existing records.", "success");
        renderValidation([], [], true);
      }
      await loadAuditHistory();
      await loadAuditHistory();
      setTimeout(function () {
        sectionData.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 120);
    } catch (error) {
      setStatus(uploadStatus, error.message || "Upload failed.", "error");
    } finally {
      restoreUploadButton();
    }
  }
  function onFormChanged() {
    var validation = validateCurrentInput();
    if (state.previewInput) {
      state.previewDirty = !sameStudy(validation.currentStudy, state.previewInput);
      if (state.previewDirty) {
        state.confirmedAudit = null;
        state.confirmedSummary = null;
      }
    }
    refreshReviewState();
    updateSummaryStripFromForm();
    persistState();
    renderFinalSummary();
    renderAuditSummary();
  }

  function refreshReviewState() {
    var validation = validateCurrentInput();
    renderCheckinAvailability(validation.currentStudy.preferred_checkin);
    renderValidation(validation.errors, validation.warnings, validation.errors.length === 0);
    applyFieldErrors(validation.errorFields);
    renderReviewMeta();
    renderChangePreview(validation.currentStudy);
    renderPreviewState();
    btnPreview.disabled = !state.uploadedFilename || validation.errors.length > 0;
    btnConfirm.disabled = !state.previewResults || state.previewDirty || validation.errors.length > 0;
    syncActionVisibility();
  }

  function syncActionVisibility() {
    btnResetUpload.classList.toggle("hidden", btnResetUpload.disabled);
    btnConfirm.classList.toggle("hidden", btnConfirm.disabled);
  }

  function updateSummaryStripFromForm() {
    var validation = validateCurrentInput();
    if (!state.clinics || state.clinics.length === 0) {
      return;
    }

    var totalBeds = state.clinics.reduce(function (sum, clinic) {
      return sum + (clinic.capacity || 0);
    }, 0);
    var totalParticipants = (validation.currentStudy.male || 0) + (validation.currentStudy.female || 0);
    var stats = [
      { value: (state.clinics || []).length, label: "Clinics", accent: false },
      { value: totalBeds, label: "Total Beds", accent: false },
      { value: (state.existingSchedule || []).length, label: "Booked Periods", accent: false },
      { value: safeValue(validation.currentStudy.protocol), label: "New Study Protocol", accent: true },
      { value: totalParticipants, label: "New Participants", accent: true },
      { value: safeValue(validation.currentStudy.los) + " d", label: "Length of Stay", accent: false }
    ];

    summaryStrip.innerHTML = stats.map(function (item) {
      return '<div class="stat-block' + (item.accent ? ' accent' : '') + '">' +
        '<div class="stat-value">' + escapeHtml(item.value) + '</div>' +
        '<div class="stat-label">' + escapeHtml(item.label) + '</div>' +
      '</div>';
    }).join("");
  }

  function validateCurrentInput() {
    var current = collectStudyFromForm();
    var errors = [];
    var warnings = [];
    var errorFields = [];

    if (!current.protocol.trim()) {
      errors.push("Protocol is required.");
      errorFields.push("protocol");
    }
    if (!current.preferred_checkin) {
      errors.push("Preferred check-in date is required.");
      errorFields.push("checkin");
    }

    [
      ["male", "Male participants", 0],
      ["female", "Female participants", 0],
      ["periods", "Number of periods", 1],
      ["washout", "Washout days", 0],
      ["los", "Length of stay", 1]
    ].forEach(function (entry) {
      var key = entry[0];
      var label = entry[1];
      var min = entry[2];
      var raw = current[key];
      var parsed = Number(raw);
      if (raw === "" || !Number.isInteger(parsed)) {
        errors.push(label + " must be a whole number.");
        errorFields.push(key);
        return;
      }
      if (parsed < min) {
        errors.push(label + " must be at least " + min + ".");
        errorFields.push(key);
      }
      current[key] = parsed;
    });

    if ((Number(current.male) || 0) + (Number(current.female) || 0) <= 0) {
      errors.push("At least one participant is required.");
      errorFields.push("male");
      errorFields.push("female");
    }

    var totalBeds = state.clinics.reduce(function (sum, clinic) {
      return sum + (clinic.capacity || 0);
    }, 0);
    if (totalBeds && ((Number(current.male) || 0) + (Number(current.female) || 0) > totalBeds)) {
      warnings.push("Participants exceed total single-day bed capacity. A shift may still make this feasible.");
    }

    return {
      currentStudy: normalizeStudy(current),
      errors: unique(errors),
      warnings: unique(warnings),
      errorFields: unique(errorFields)
    };
  }

  function parseClinicAllocationRaw(raw) {
    var map = {};
    if (!raw) {
      return map;
    }
    var regex = /(\w+)\s*\((\d+)\)/g;
    var match;
    while ((match = regex.exec(String(raw))) !== null) {
      map[match[1]] = Number(match[2]) || 0;
    }
    return map;
  }

  function renderCheckinAvailability(checkinIso) {
    ensureCheckinAvailabilityElement();
    if (!checkinAvailabilityEl) {
      return;
    }
    if (!state.clinics || !state.clinics.length) {
      checkinAvailabilityEl.textContent = "Upload a file to view check-in date bed availability.";
      return;
    }
    if (!checkinIso) {
      checkinAvailabilityEl.textContent = "Select preferred check-in date to view free beds.";
      return;
    }

    var occupancy = {};
    var capacities = {};
    state.clinics.forEach(function (clinic) {
      var cap = Number(clinic.capacity || 0);
      capacities[clinic.id] = cap;
      occupancy[clinic.id] = 0;
    });

    (state.existingSchedule || []).forEach(function (row) {
      var rowCheckin = row.checkin || "";
      var rowCheckout = row.checkout || "";
      if (!rowCheckin || !rowCheckout) {
        return;
      }
      if (checkinIso < rowCheckin || checkinIso > rowCheckout) {
        return;
      }

      var maleMap = parseClinicAllocationRaw(row.male_clinic);
      var femaleMap = parseClinicAllocationRaw(row.female_clinic);

      Object.keys(maleMap).forEach(function (cid) {
        if (occupancy[cid] == null) {
          occupancy[cid] = 0;
          capacities[cid] = capacities[cid] || 0;
        }
        occupancy[cid] += Number(maleMap[cid] || 0);
      });
      Object.keys(femaleMap).forEach(function (cid) {
        if (occupancy[cid] == null) {
          occupancy[cid] = 0;
          capacities[cid] = capacities[cid] || 0;
        }
        occupancy[cid] += Number(femaleMap[cid] || 0);
      });
    });

    var totalFree = 0;
    var details = Object.keys(capacities).map(function (cid) {
      var free = Math.max(0, Number(capacities[cid] || 0) - Number(occupancy[cid] || 0));
      totalFree += free;
      return "Clinic " + cid + ": " + free;
    }).join(" | ");

    checkinAvailabilityEl.innerHTML =
      '<span class="checkin-availability-title">Beds free on ' + escapeHtml(checkinIso) + ':</span> ' +
      '<strong>' + escapeHtml(totalFree) + '</strong>' +
      '<div class="checkin-availability-meta">' + escapeHtml(details) + '</div>';
  }

  async function previewSchedule() {
    var validation = validateCurrentInput();
    refreshReviewState();
    if (validation.errors.length > 0 || !state.audit) {
      return;
    }

    btnPreview.disabled = true;
    btnPreview.innerHTML = '<span class="spinner"></span> Previewing...';

    try {
      var response = await fetchJson("/api/schedule-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: state.uploadedFilename,
          transaction_id: state.audit.transaction_id,
          new_study: validation.currentStudy
        })
      });

      state.previewInput = cloneValue(response.preview_input || validation.currentStudy);
      state.previewSummary = response.summary || null;
      state.previewResults = response.results || [];
      state.previewDirty = false;
      state.confirmedAudit = null;
      state.confirmedSummary = null;
      persistState();
      btnResetUpload.disabled = false;

      renderResults(state.previewResults);
      renderFinalSummary();
      renderAuditSummary();
      renderPreviewState();
      btnConfirm.disabled = false;
      sectionResults.classList.remove("hidden");
      requestAnimationFrame(function () {
        sectionResults.classList.add("fade-up");
      });
      markStepDone(step2El);
      markStepActive(step3El);
      setTimeout(function () {
        sectionResults.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 120);
    } catch (error) {
      if (error.data && Array.isArray(error.data.errors)) {
        renderValidation(error.data.errors, [], false);
      } else {
        renderValidation([error.message || "Preview failed."], [], false);
      }
    } finally {
      restorePreviewButton();
      refreshReviewState();
    }
  }

  async function confirmSchedule() {
    var validation = validateCurrentInput();
    refreshReviewState();
    if (validation.errors.length > 0 || !state.previewResults || state.previewDirty || !state.audit) {
      return;
    }

    btnConfirm.disabled = true;
    btnConfirm.innerHTML = '<span class="spinner"></span> Storing...';

    try {
      var response = await fetchJson("/api/schedule-confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: state.uploadedFilename,
          transaction_id: state.audit.transaction_id,
          new_study: validation.currentStudy
        })
      });

      state.confirmedAudit = response.audit || null;
      state.confirmedSummary = response.summary || state.previewSummary;
      state.previewResults = response.results || state.previewResults;
      state.previewSummary = response.summary || state.previewSummary;
      state.previewDirty = false;
      persistState();

      renderResults(state.previewResults);
      renderFinalSummary();
      renderAuditSummary();
      renderPreviewState();
      await loadAuditHistory();
    } catch (error) {
      if (error.data && Array.isArray(error.data.errors)) {
        renderValidation(error.data.errors, [], false);
      } else {
        renderValidation([error.message || "Confirm failed."], [], false);
      }
    } finally {
      restoreConfirmButton();
      refreshReviewState();
    }
  }

  function renderSummaryStrip(data) {
    var study = data.new_study || {};
    var totalBeds = (data.clinics || []).reduce(function (sum, clinic) {
      return sum + (clinic.capacity || 0);
    }, 0);
    var totalParticipants = (study.male || 0) + (study.female || 0);
    var stats = [
      { value: (data.clinics || []).length, label: "Clinics", accent: false },
      { value: totalBeds, label: "Total Beds", accent: false },
      { value: (data.existing_schedule || []).length, label: "Booked Periods", accent: false },
      { value: safeValue(study.protocol), label: "New Study Protocol", accent: true },
      { value: totalParticipants, label: "New Participants", accent: true },
      { value: safeValue(study.los) + " d", label: "Length of Stay", accent: false }
    ];

    summaryStrip.innerHTML = stats.map(function (item) {
      return '<div class="stat-block' + (item.accent ? ' accent' : '') + '">' +
        '<div class="stat-value">' + escapeHtml(item.value) + '</div>' +
        '<div class="stat-label">' + escapeHtml(item.label) + '</div>' +
      '</div>';
    }).join("");
  }

  function renderClinicGrid(clinics) {
    var maxCapacity = Math.max.apply(null, clinics.map(function (clinic) {
      return clinic.capacity || 0;
    }).concat([1]));

    clinicGrid.innerHTML = clinics.map(function (clinic) {
      var width = Math.round(((clinic.capacity || 0) / maxCapacity) * 100);
      return '<div class="clinic-card">' +
        '<div class="clinic-id">' + escapeHtml(clinic.id) + '</div>' +
        '<div class="clinic-cap">' + escapeHtml(clinic.capacity || 0) + ' beds</div>' +
        '<div class="clinic-bar"><div class="clinic-fill" style="width:' + width + '%"></div></div>' +
      '</div>';
    }).join("");
  }

  function renderExistingTable(schedule) {
    existingCount.textContent = schedule.length + " records";
    existingTbody.innerHTML = schedule.map(function (row) {
      return '<tr>' +
        '<td><span class="proto-pill">' + escapeHtml(row.protocol) + '</span></td>' +
        '<td><span class="period-pill">' + escapeHtml(row.period) + '</span></td>' +
        '<td class="num-text">' + escapeHtml(row.male || 0) + '</td>' +
        '<td class="num-text">' + escapeHtml(row.female || 0) + '</td>' +
        '<td class="clinic-raw">' + escapeHtml(row.male_clinic) + '</td>' +
        '<td class="clinic-raw">' + escapeHtml(row.female_clinic) + '</td>' +
        '<td class="date-text">' + escapeHtml(row.checkin) + '</td>' +
        '<td class="date-text">' + escapeHtml(row.checkout) + '</td>' +
        '<td class="num-text">' + escapeHtml(row.los || 0) + '</td>' +
      '</tr>';
    }).join("");
  }

  function fillStudyForm(study) {
    nsFields.protocol.value = study.protocol || "";
    nsFields.male.value = study.male != null ? study.male : 0;
    nsFields.female.value = study.female != null ? study.female : 0;
    nsFields.periods.value = study.periods != null ? study.periods : 1;
    nsFields.washout.value = study.washout != null ? study.washout : 0;
    nsFields.los.value = study.los != null ? study.los : 1;
    nsFields.checkin.value = study.preferred_checkin || "";
  }

  function renderValidation(errors, warnings, isReady) {
    if (errors.length === 0 && warnings.length === 0 && !state.uploadedFilename) {
      validationSummary.classList.add("hidden");
      validationSummary.innerHTML = "";
      return;
    }

    var type = errors.length ? "error" : (warnings.length ? "warning" : "ok");
    var title = errors.length ? "Fix before preview" : (warnings.length ? "Review warnings" : "Ready to preview");
    var lines = errors.length ? errors : (warnings.length ? warnings : ["Input is valid. Preview the schedule, then confirm to store final linked records in SQLite."]);

    validationSummary.className = "validation-summary " + type;
    validationSummary.innerHTML =
      '<div class="validation-title">' + escapeHtml(title) + '</div>' +
      '<div class="validation-list">' + lines.map(function (line) {
        return '<div>' + escapeHtml(line) + '</div>';
      }).join("") + '</div>';
    validationSummary.classList.remove("hidden");
  }

  function applyFieldErrors(errorFields) {
    Object.keys(nsFields).forEach(function (key) {
      nsFields[key].classList.remove("field-error");
    });

    errorFields.forEach(function (field) {
      if (field === "checkin") {
        nsFields.checkin.classList.add("field-error");
      } else if (nsFields[field]) {
        nsFields[field].classList.add("field-error");
      }
    });
  }

  function renderReviewMeta() {
    if (!state.audit) {
      reviewMeta.innerHTML = '<div class="empty-state">Upload a file to generate transaction, file, and input records.</div>';
      return;
    }

    reviewMeta.innerHTML = [
      metaChip("Transaction", state.audit.transaction_id),
      metaChip("File ID", state.audit.file_id),
      metaChip("Upload Op", state.audit.upload_operation_id),
      metaChip("Source Input", state.audit.source_input_record_id),
      metaChip("Storage", state.audit.storage || "sqlite"),
      metaChip("File", state.pendingFile ? state.pendingFile.name : state.uploadedFilename)
    ].join("");
  }

  function renderChangePreview(currentStudy) {
    var original = state.originalStudy || {};
    var current = currentStudy || normalizeStudy(collectStudyFromForm());
    var changes = 0;

    changePreview.innerHTML = fieldConfig.map(function (field) {
      var originalValue = getStudyField(original, field.key);
      var currentValue = getStudyField(current, field.key);
      var changed = String(originalValue) !== String(currentValue);
      if (changed) {
        changes += 1;
      }
      return '<div class="change-row' + (changed ? ' changed' : '') + '">' +
        '<div class="change-head">' +
          '<span class="change-label">' + escapeHtml(field.label) + '</span>' +
          '<span class="change-status">' + (changed ? 'Changed' : 'Same') + '</span>' +
        '</div>' +
        '<div class="change-values">' +
          '<div class="change-block"><span class="change-caption">Uploaded</span><span class="change-value">' + escapeHtml(formatPreviewValue(originalValue)) + '</span></div>' +
          '<div class="change-arrow">&rarr;</div>' +
          '<div class="change-block"><span class="change-caption">Current</span><span class="change-value">' + escapeHtml(formatPreviewValue(currentValue)) + '</span></div>' +
        '</div>' +
      '</div>';
    }).join("");

    changeCount.textContent = changes + (changes === 1 ? " change" : " changes");
  }

  function renderPreviewState() {
    if (!state.audit) {
      previewStatus.textContent = "Upload a file to begin.";
      previewBanner.textContent = "This is a preview of the schedule outcome. Use Confirm & Store to create linked output records.";
      previewBanner.className = "preview-banner";
      return;
    }

    if (!state.previewResults) {
      previewStatus.textContent = "Preview first. Confirm only after you review the proposed allocations.";
      previewBanner.textContent = "This is a preview-only workflow. No final output record is written until you click Confirm & Store.";
      previewBanner.className = "preview-banner";
      return;
    }

    if (state.previewDirty) {
      previewStatus.textContent = "Inputs changed after preview. Re-preview before confirming.";
      previewBanner.textContent = "Preview is stale because the request changed after the last calculation.";
      previewBanner.className = "preview-banner stale";
      return;
    }

    if (state.confirmedAudit) {
      previewStatus.textContent = "Confirmed and stored in SQLite.";
      previewBanner.textContent = "Final output record stored. This transaction is now fully linked across upload, input, output, and operations.";
      previewBanner.className = "preview-banner ready";
      return;
    }

    previewStatus.textContent = "Preview is ready. Confirm now to persist the final output.";
    previewBanner.textContent = "Preview complete. Review the allocations below, then click Confirm & Store to create the final output record.";
    previewBanner.className = "preview-banner ready";
  }

  function renderResults(results) {
    strategyCards.innerHTML = "";
    if (!results.length) {
      strategyCards.innerHTML = '<div class="empty-state">Preview the schedule to see strategy outcomes here.</div>';
      return;
    }

    results.forEach(function (result, index) {
      var meta = strategyMeta[result.strategy] || { icon: "SR", desc: "Scheduling result" };
      var isFeasible = !!result.feasible;
      var isOptional = !!result.optional;
      var isOpenByDefault = true;
      var card = document.createElement("div");
      var cardClass = isOptional ? "optional" : (isFeasible ? "feasible" : "infeasible");
      card.className = "strat-card " + cardClass;
      card.style.animationDelay = (index * 80) + "ms";
      card.classList.add("fade-up");
      
      var badgeText = isOptional ? "Optional" : (isFeasible ? "Feasible" : "Not Feasible");
      var badgeClass = isOptional ? "optional" : (isFeasible ? "ok" : "no");
      
      card.innerHTML =
        '<div class="strat-header" role="button" aria-expanded="' + String(isOpenByDefault) + '">' +
          '<div class="strat-icon-wrap">' + escapeHtml(meta.icon) + '</div>' +
          '<div class="strat-header-text">' +
            '<div class="strat-name">Strategy ' + (index + 1) + ': ' + escapeHtml(result.strategy) + '</div>' +
            '<div class="strat-short">' + escapeHtml(meta.desc) + '</div>' +
          '</div>' +
          '<span class="status-badge ' + badgeClass + '">' + badgeText + '</span>' +
          '<svg class="chevron ' + (isOpenByDefault ? 'open' : '') + '" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>' +
        '</div>' +
        '<div class="strat-body ' + (isOpenByDefault ? 'visible' : '') + '">' +
          (result.note ? '<div class="strat-note">' + escapeHtml(result.note) + '</div>' : '') +
          renderBedAvailability(result) +
          (isOptional
            ? renderAlternativeDates(result.alternatives || [])
            : (isFeasible
              ? renderPeriods(result.periods || [], result.shift_days || 0, result.preferred_date_block_reason || '')
              : renderInfeasibleReason(result.preferred_date_block_reason || 'No feasible schedule could be found under current constraints.'))) +
        '</div>';

      card.querySelector(".strat-header").addEventListener("click", function () {
        var body = card.querySelector(".strat-body");
        var chevron = card.querySelector(".chevron");
        var isOpen = body.classList.contains("visible");
        body.classList.toggle("visible", !isOpen);
        chevron.classList.toggle("open", !isOpen);
      });
      strategyCards.appendChild(card);
    });
  }

  function renderPeriods(periods, shiftDays, blockReason) {
    var banner = '';
    if (shiftDays) {
      banner = '<div class="shift-banner">' +
        'Check-in shifted by <strong>' + escapeHtml(shiftDays) + ' day' + (shiftDays !== 1 ? 's' : '') + '</strong> from the preferred date' +
        (blockReason ? '<div class="shift-reason"><span class="shift-reason-label">Why preferred date was unavailable:</span> ' + escapeHtml(blockReason) + '</div>' : '') +
        '</div>';
    }

    return banner + periods.map(function (period) {
      return '<div class="period-block">' +
        '<div class="period-head">' +
          '<span class="period-label">Period ' + escapeHtml(period.period_num) + '</span>' +
          '<span class="period-dates">' + escapeHtml(period.checkin_date) + ' &rarr; ' + escapeHtml(period.checkout_date) + '</span>' +
        '</div>' +
        '<div class="period-body">' +
          '<div class="alloc-row"><span class="alloc-gender male">Males</span><div class="alloc-chips">' + clinicChips(period.male_clinics) + '</div></div>' +
          '<div class="alloc-row"><span class="alloc-gender female">Females</span><div class="alloc-chips">' + clinicChips(period.female_clinics) + '</div></div>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function renderInfeasibleReason(reasonText) {
    return '<div class="shift-banner">' +
      '<div class="shift-reason"><span class="shift-reason-label">Why not feasible:</span> ' + escapeHtml(reasonText) + '</div>' +
      '</div>';
  }

  function renderBedAvailability(result) {
    var date = result.evaluated_checkin_date || '';
    var total = result.checkin_beds_free_total;
    var map = result.checkin_beds_free || {};
    var keys = Object.keys(map);

    if (!keys.length || total == null) {
      return '';
    }

    var details = keys.map(function (cid) {
      return 'Clinic ' + cid + ': ' + map[cid];
    }).join(' | ');

    return '<div class="strat-note">' +
      'Beds free on ' + escapeHtml(date) + ': <strong>' + escapeHtml(total) + '</strong>' +
      '<div class="shift-reason">' + escapeHtml(details) + '</div>' +
      '</div>';
  }

  function renderAlternativeDates(alternatives) {
    if (!alternatives || alternatives.length === 0) {
      return '<div class="alternatives-empty">No alternative shift dates found within ' + MAX_SHIFT_DAYS + ' days.</div>';
    }

    var html = '<div class="alternatives-list">' +
      '<div class="alternatives-header">Available dates for simple shift (no split):</div>';
    
    alternatives.forEach(function (alt) {
      html += '<div class="alternative-item">' +
        '<div class="alt-date">' + escapeHtml(alt.checkin_date) + '</div>' +
        '<div class="alt-meta">' +
          'Shift: +' + escapeHtml(alt.shift_days) + ' day' + (alt.shift_days !== 1 ? 's' : '') + ' | ' +
          'Free beds: ' + escapeHtml(alt.beds_free) +
        '</div>' +
        '</div>';
    });

    html += '</div>';
    return html;
  }

  function clinicChips(map) {
    if (!map || !Object.keys(map).length) {
      return '<span class="chip chip-none">None</span>';
    }
    return Object.keys(map).map(function (clinicId) {
      return '<span class="chip chip-clinic">Clinic ' + escapeHtml(clinicId) + ' <span class="chip-count">x ' + escapeHtml(map[clinicId]) + '</span></span>';
    }).join("");
  }

  function renderFinalSummary() {
    if (!state.previewSummary) {
      finalSummary.innerHTML = '<div class="empty-state">Preview the schedule to generate the final review summary.</div>';
      return;
    }

    var summary = state.confirmedSummary || state.previewSummary;
    var mode = state.confirmedAudit ? "Confirmed" : "Preview";
    finalSummary.innerHTML = [
      summaryCard("Mode", mode, state.confirmedAudit ? "Stored in SQLite" : "Not stored yet"),
      summaryCard("Recommended strategy", safeValue(summary.recommended_strategy), safeValue(summary.recommended_note)),
      summaryCard("Feasible strategies", safeValue(summary.feasible_count) + " / " + safeValue(summary.total_strategies), "Across all strategies"),
      summaryCard("Shift days", safeValue(summary.recommended_shift_days), "Best strategy date movement")
    ].join("");
  }

  function renderAuditSummary() {
    if (!state.audit) {
      auditSummary.innerHTML = '<div class="empty-state">Audit IDs appear here after upload.</div>';
      return;
    }

    var rows = [
      auditRow("Transaction", state.audit.transaction_id),
      auditRow("File ID", state.audit.file_id),
      auditRow("Upload Op", state.audit.upload_operation_id),
      auditRow("Source Input", state.audit.source_input_record_id),
      auditRow("Storage", state.audit.storage || "sqlite")
    ];

    if (state.confirmedAudit) {
      rows.push(auditRow("Request Input", state.confirmedAudit.request_input_record_id));
      rows.push(auditRow("Output Record", state.confirmedAudit.output_record_id));
      rows.push(auditRow("Schedule Op", state.confirmedAudit.schedule_operation_id));
      rows.push(auditRow("Stored At", state.confirmedAudit.stored_at));
    } else {
      rows.push(auditRow("Output Record", "Pending confirmation"));
    }

    auditSummary.innerHTML = rows.join("");
  }

  async function loadAuditHistory() {
    try {
      var response = await fetchJson("/api/audit-log?limit=6", { method: "GET" });
      renderAuditHistory(response.items || []);
    } catch (_error) {
      renderAuditHistory([]);
    }
  }

  function renderAuditHistory(items) {
    if (!items.length) {
      auditHistory.innerHTML = '<div class="empty-state">No stored activity yet.</div>';
      return;
    }

    auditHistory.innerHTML = items.map(function (item) {
      return '<div class="history-item">' +
        '<div class="history-top"><div class="history-id">' + escapeHtml(item.transaction_id) + ' / ' + escapeHtml(item.file_id) + '</div><div class="history-status ' + escapeHtml(item.status || '') + '">' + escapeHtml(item.status || 'unknown') + '</div></div>' +
        '<div class="history-meta">' +
          '<span>' + escapeHtml(item.filename || '-') + '</span>' +
          '<span>Updated: ' + escapeHtml(item.updated_at || '-') + '</span>' +
          '<span>Recommended: ' + escapeHtml(item.recommended_strategy || 'n/a') + '</span>' +
          '<span>Storage: ' + escapeHtml(item.storage || 'sqlite') + '</span>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function resetPreviewState() {
    state.previewInput = null;
    state.previewSummary = null;
    state.previewResults = null;
    state.previewDirty = false;
    state.confirmedAudit = null;
    state.confirmedSummary = null;
  }

  function resetUploadContext() {
    state.pendingFile = null;
    state.uploadedFilename = null;
    state.originalStudy = null;
    state.audit = null;
    state.clinics = [];
    state.existingSchedule = [];
    resetPreviewState();

    clearPersistedState();
    fileInput.value = "";
    dropZone.classList.remove("has-file");
    dropZone.querySelector(".dropzone-main").textContent = "Drop your Excel file here";
    dropZone.querySelector(".dropzone-hint").innerHTML = 'or <span class="link-text">click to browse files</span>';

    sectionData.classList.add("hidden");
    sectionResults.classList.add("hidden");
    summaryStrip.innerHTML = "";
    clinicGrid.innerHTML = "";
    existingTbody.innerHTML = "";
    existingCount.textContent = "";
    btnUpload.disabled = true;
    btnResetUpload.disabled = true;
    syncActionVisibility();
    setStatus(uploadStatus, "", "");
    renderValidation([], [], true);
    renderReviewMeta();
    renderChangePreview();
    renderResults([]);
    renderFinalSummary();
    renderAuditSummary();
    setStepProgressFromState();
    refreshReviewState();
  }

  function restorePlannerFromState() {
    setStepProgressFromState();

    if (!state.uploadedFilename) {
      btnResetUpload.disabled = true;
      syncActionVisibility();
      sectionData.classList.add("hidden");
      sectionResults.classList.add("hidden");
      return;
    }

    sectionData.classList.remove("hidden");

    var seedStudy = state.previewInput || state.originalStudy || {};
    fillStudyForm(seedStudy);
    renderCheckinAvailability(seedStudy.preferred_checkin || "");

    renderSummaryStrip({
      new_study: state.originalStudy || seedStudy,
      clinics: state.clinics || [],
      existing_schedule: state.existingSchedule || []
    });
    renderClinicGrid(state.clinics || []);
    renderExistingTable(state.existingSchedule || []);

    dropZone.classList.add("has-file");
    dropZone.querySelector(".dropzone-main").textContent = state.uploadedFilename;
    dropZone.querySelector(".dropzone-hint").textContent = "Previously uploaded file loaded. Upload a new file to replace it.";
    state.pendingFile = null;
    btnUpload.disabled = true;
    btnResetUpload.disabled = false;
    syncActionVisibility();

    if (state.previewResults && state.previewResults.length) {
      sectionResults.classList.remove("hidden");
      renderResults(state.previewResults);
    } else {
      sectionResults.classList.add("hidden");
    }
    syncActionVisibility();
  }

  function resetStepStates() {
    var steps = [
      { el: step1El, num: "1" },
      { el: step2El, num: "2" },
      { el: step3El, num: "3" }
    ];

    steps.forEach(function (item) {
      item.el.classList.remove("active", "done");
      item.el.querySelector(".step-circle").textContent = item.num;
    });
    step1El.classList.add("active");
  }

  function setStepProgressFromState() {
    resetStepStates();

    if (!state.uploadedFilename) {
      return;
    }

    markStepDone(step1El);
    markStepActive(step2El);

    if (state.previewResults && state.previewResults.length) {
      markStepDone(step2El);
      markStepActive(step3El);
    }

    if (state.confirmedAudit) {
      markStepDone(step3El);
    }
  }

  function persistState() {
    try {
      var payload = {
        uploadedFilename: state.uploadedFilename,
        originalStudy: state.originalStudy,
        audit: state.audit,
        clinics: state.clinics,
        existingSchedule: state.existingSchedule,
        previewInput: state.previewInput,
        previewSummary: state.previewSummary,
        previewResults: state.previewResults,
        previewDirty: state.previewDirty,
        confirmedAudit: state.confirmedAudit,
        confirmedSummary: state.confirmedSummary
      };
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (_err) {
      // Ignore storage errors (private mode, quota, disabled storage).
    }
  }

  function clearPersistedState() {
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (_err) {
      // Ignore storage errors.
    }
  }

  function isReloadNavigation() {
    try {
      var entries = window.performance && window.performance.getEntriesByType
        ? window.performance.getEntriesByType("navigation")
        : [];
      if (entries && entries.length > 0) {
        return entries[0].type === "reload";
      }
      if (window.performance && window.performance.navigation) {
        return window.performance.navigation.type === 1;
      }
    } catch (_err) {
      // Ignore performance API issues.
    }
    return false;
  }

  function restorePersistedState() {
    try {
      var raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return false;
      }

      var saved = JSON.parse(raw);
      if (!saved || typeof saved !== "object") {
        return false;
      }

      state.uploadedFilename = saved.uploadedFilename || null;
      state.originalStudy = cloneValue(saved.originalStudy || null);
      state.audit = cloneValue(saved.audit || null);
      state.clinics = cloneValue(saved.clinics || []);
      state.existingSchedule = cloneValue(saved.existingSchedule || []);
      state.previewInput = cloneValue(saved.previewInput || null);
      state.previewSummary = cloneValue(saved.previewSummary || null);
      state.previewResults = cloneValue(saved.previewResults || null);
      state.previewDirty = !!saved.previewDirty;
      state.confirmedAudit = cloneValue(saved.confirmedAudit || null);
      state.confirmedSummary = cloneValue(saved.confirmedSummary || null);
      return true;
    } catch (_err) {
      return false;
    }
  }

  function collectStudyFromForm() {
    return {
      protocol: nsFields.protocol.value.trim(),
      male: nsFields.male.value,
      female: nsFields.female.value,
      periods: nsFields.periods.value,
      washout: nsFields.washout.value,
      los: nsFields.los.value,
      preferred_checkin: nsFields.checkin.value
    };
  }

  function normalizeStudy(raw) {
    return {
      protocol: String(raw.protocol || "").trim(),
      male: whole(raw.male),
      female: whole(raw.female),
      periods: whole(raw.periods),
      washout: whole(raw.washout),
      los: whole(raw.los),
      preferred_checkin: String(raw.preferred_checkin || "")
    };
  }

  function sameStudy(left, right) {
    return JSON.stringify(left || {}) === JSON.stringify(right || {});
  }

  function getStudyField(study, key) {
    return key === "preferred_checkin" ? (study.preferred_checkin || "") : study[key];
  }

  function metaChip(label, value) {
    return '<span class="meta-chip"><b>' + escapeHtml(label) + ':</b> ' + escapeHtml(value || '-') + '</span>';
  }

  function summaryCard(label, value, note) {
    return '<div class="summary-card"><div class="summary-label">' + escapeHtml(label) + '</div><div class="summary-value">' + escapeHtml(value) + '</div><div class="summary-note">' + escapeHtml(note) + '</div></div>';
  }

  function auditRow(label, value) {
    return '<div class="audit-row"><div class="audit-label">' + escapeHtml(label) + '</div><div class="audit-value">' + escapeHtml(value || '-') + '</div></div>';
  }

  function restoreUploadButton() {
    btnUpload.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/><path d="M5 19h14"/></svg> Analyse File';
    btnUpload.disabled = !state.pendingFile;
  }

  function restorePreviewButton() {
    btnPreview.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Preview Schedule';
  }

  function restoreConfirmButton() {
    btnConfirm.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg> Confirm & Store';
  }

  function markStepDone(element) {
    element.classList.remove("active");
    element.classList.add("done");
    element.querySelector(".step-circle").innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  }

  function markStepActive(element) {
    element.classList.add("active");
  }

  function setStatus(element, message, type) {
    element.textContent = message;
    element.className = 'inline-status' + (type ? ' ' + type : '');
  }

  function fetchJson(url, options) {
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var data = {};

        if (text) {
          try {
            data = JSON.parse(text);
          } catch (_err) {
            data = { error: text.slice(0, 180) };
          }
        }

        if (!response.ok) {
          var error = new Error((data && data.error) || response.statusText || 'Request failed.');
          error.data = data;
          error.status = response.status;
          throw error;
        }
        return data;
      });
    }).catch(function (error) {
      if (error && error.name === 'TypeError') {
        throw new Error('Network error while contacting the server. Please retry.');
      }
      throw error;
    });
  }

  async function fetchJsonWithRetry(url, options, maxAttempts, retryDelayMs) {
    var attempts = Math.max(1, maxAttempts || 1);
    var delayMs = Math.max(0, retryDelayMs || 0);
    var lastError = null;

    for (var i = 0; i < attempts; i++) {
      try {
        return await fetchJson(url, options);
      } catch (error) {
        lastError = error;
        if (i === attempts - 1) {
          throw error;
        }
        if ((error.message || '').toLowerCase().indexOf('network error') === -1) {
          throw error;
        }
        await new Promise(function (resolve) {
          setTimeout(resolve, delayMs);
        });
      }
    }

    throw lastError || new Error('Request failed.');
  }

  function whole(value) {
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
  }

  function safeValue(value) {
    return value == null || value === '' ? '-' : value;
  }

  function formatPreviewValue(value) {
    return value == null || value === '' ? 'Not set' : value;
  }

  function formatFileSize(size) {
    if (size < 1024) {
      return size + ' B';
    }
    if (size < 1024 * 1024) {
      return (size / 1024).toFixed(1) + ' KB';
    }
    return (size / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function cloneValue(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function unique(items) {
    return Array.from(new Set(items));
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
