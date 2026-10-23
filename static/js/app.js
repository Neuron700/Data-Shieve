(function () {
  "use strict";

  function onReady(callback) {
    if (document.readyState === "complete") {
      callback();
    } else {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    }
  }

  function setupSidebar() {
    var toggle = document.getElementById("menu-toggle");
    var sidebar = document.getElementById("sidebar");
    var backdrop = document.getElementById("sidebar-backdrop");
    if (!toggle || !sidebar || !backdrop) return;

    function setOpen(open) {
      sidebar.classList.toggle("open", open);
      backdrop.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
      document.body.style.overflow = open ? "hidden" : "";
    }

    toggle.addEventListener("click", function () {
      setOpen(!sidebar.classList.contains("open"));
    });
    backdrop.addEventListener("click", function () {
      setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && sidebar.classList.contains("open")) {
        setOpen(false);
        toggle.focus();
      }
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 900 && sidebar.classList.contains("open")) {
        setOpen(false);
      }
    });
  }

  function dismissToast(toast) {
    if (!toast || !toast.parentNode) return;
    toast.style.transition = "opacity 160ms ease, transform 160ms ease";
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-6px)";
    window.setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 170);
  }

  function setupToasts() {
    document.querySelectorAll("[data-toast]").forEach(function (toast) {
      var button = toast.querySelector("[data-dismiss-toast]");
      if (button) {
        button.addEventListener("click", function () {
          dismissToast(toast);
        });
      }
      var timer = window.setTimeout(function () {
        dismissToast(toast);
      }, 8000);
      toast.addEventListener("mouseenter", function () {
        window.clearTimeout(timer);
      });
      toast.addEventListener("focusin", function () {
        window.clearTimeout(timer);
      });
    });
  }

  function setupDropZone() {
    var zone = document.getElementById("drop-zone");
    var input = zone ? zone.querySelector('input[type="file"]') : null;
    var label = document.getElementById("file-name");
    if (!zone || !input || !label) return;

    function describe(file) {
      label.classList.remove("text-danger");
      if (!file) {
        label.textContent = "";
        label.classList.remove("visible");
        return;
      }
      var maxMb = Number(zone.getAttribute("data-max-mb") || 0);
      if (maxMb && file.size > maxMb * 1024 * 1024) {
        label.textContent = "That file is larger than " + maxMb + " MB. Choose a smaller file.";
        label.classList.add("visible", "text-danger");
        input.value = "";
        return;
      }
      var size = file.size;
      var units = ["B", "KB", "MB", "GB"];
      var index = 0;
      while (size >= 1024 && index < units.length - 1) {
        size /= 1024;
        index += 1;
      }
      var readable = index === 0 ? size + " " + units[index] : size.toFixed(1) + " " + units[index];
      label.textContent = file.name + " · " + readable;
      label.classList.add("visible");
    }

    input.addEventListener("change", function () {
      describe(input.files && input.files[0]);
    });
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.remove("dragover");
      });
    });
    zone.addEventListener("drop", function (event) {
      if (!event.dataTransfer || !event.dataTransfer.files.length) return;
      try {
        input.files = event.dataTransfer.files;
      } catch (error) {
        label.textContent = "This browser cannot attach a dropped file. Use Choose file instead.";
        label.classList.add("visible", "text-danger");
        return;
      }
      describe(input.files[0]);
    });
  }

  function setupAutoSubmit() {
    document.querySelectorAll("[data-auto-submit]").forEach(function (control) {
      control.addEventListener("change", function () {
        var form = control.form;
        if (!form) return;
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
      });
    });
  }

  function setupCleaningForm() {
    var select = document.getElementById("id_operation");
    if (!select) return;
    var panels = document.querySelectorAll("[data-operation-panel]");
    var summaryTitle = document.getElementById("operation-summary-title");
    var summaryText = document.getElementById("operation-summary-text");
    var operationData = document.getElementById("operation-data");

    function updateSummary(operation) {
      if (!operationData || !summaryTitle || !summaryText) return;
      var source = operationData.querySelector('[data-operation="' + operation + '"]');
      if (!source) return;
      summaryTitle.textContent = source.getAttribute("data-label") || "";
      summaryText.textContent = [
        source.getAttribute("data-description") || "",
        source.getAttribute("data-note") || "",
      ]
        .filter(Boolean)
        .join(" ");
    }

    function updatePanels(operation) {
      panels.forEach(function (panel) {
        var match = panel.getAttribute("data-operation-panel");
        panel.classList.toggle("visible", match === operation);
      });
      updateSummary(operation);
    }

    select.addEventListener("change", function () {
      updatePanels(select.value);
    });
    updatePanels(select.value);
  }

  function setupColumnChart() {
    var canvas = document.getElementById("column-quality-chart");
    var source = document.getElementById("column-quality-data");
    if (!canvas || !source || typeof window.Chart === "undefined") return;

    var chartData;
    try {
      chartData = JSON.parse(source.textContent);
    } catch (error) {
      return;
    }
    var labels = Array.isArray(chartData.labels) ? chartData.labels : [];
    var values = Array.isArray(chartData.values) ? chartData.values : [];
    if (!labels.length) return;

    var colors = values.map(function (value) {
      var score = Number(value);
      if (!Number.isFinite(score)) return "#94a3b8";
      if (score < 40) return "#dc4c4c";
      if (score < 70) return "#d97706";
      return "#0b7a75";
    });

    if (window.Chart.defaults && window.Chart.defaults.font) {
      window.Chart.defaults.font.family =
        '"Inter", "Segoe UI", ui-sans-serif, system-ui, sans-serif';
      window.Chart.defaults.color = "#5b6b7c";
    }

    new window.Chart(canvas, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Column quality score",
            data: values,
            backgroundColor: colors,
            borderRadius: 4,
            borderSkipped: false,
            maxBarThickness: 26,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 420 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (context) {
                return " Score " + context.parsed.y + " / 100";
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              maxRotation: 38,
              minRotation: 0,
              autoSkip: true,
              maxTicksLimit: 18,
              font: { size: 10 },
            },
          },
          y: {
            beginAtZero: true,
            max: 100,
            border: { display: false },
            grid: { color: "#edf2f7" },
            ticks: { stepSize: 25, font: { size: 10 } },
          },
        },
      },
    });
  }

  function setupHtmx() {
    if (typeof window.htmx === "undefined") return;
    document.body.addEventListener("htmx:configRequest", function (event) {
      var token = document.querySelector("[name=csrfmiddlewaretoken]");
      if (token && event.detail && event.detail.headers) {
        event.detail.headers["X-CSRFToken"] = token.value;
      }
    });
  }

  onReady(function () {
    setupSidebar();
    setupToasts();
    setupDropZone();
    setupAutoSubmit();
    setupCleaningForm();
    setupColumnChart();
    setupHtmx();
  });
})();
