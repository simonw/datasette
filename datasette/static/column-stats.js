// Column statistics: a modal dialog that summarises a single column by
// running aggregate SQL queries against the table or query results. The SQL
// is built here in JavaScript and executed through the database's
// /-/query.json endpoint, so the feature is only offered when the server says
// the current actor can execute arbitrary SQL against this database
// (_datasetteTableData.executeSql or _datasetteQueryData.executeSql).
//
// Pages do not load this file up front. _column_stats_loader.html imports it
// the first time the dialog is opened, so it must work as a module as well
// as a classic script.
(() => {
  var DIALOG_ID = "column-stats-dialog";
  var TITLE_ID = "column-stats-title";
  var TOP_VALUES_LIMIT = 10;
  var MAX_DISPLAY_LENGTH = 200;
  var HISTOGRAM_TARGET_BINS = 20;
  var PERCENTILES = [0, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1];
  // Whitespace characters trimmed when deciding if a string is blank.
  var WHITESPACE = "' ' || char(9) || char(10) || char(13)";
  var ISO_DATE_GLOB = "'[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]*'";

  // SQL builders ===========================================================
  // Every builder returns a complete, standalone SQL string so each query can
  // also be opened in the SQL editor. Builders read from a "source": either
  // the table itself or, when the table page has filters applied, a CTE that
  // selects the column from just the matching rows.

  var FILTERED_CTE = "filtered_rows";
  var QUERY_CTE = "query_rows";

  function quoteIdentifier(name) {
    return '"' + String(name).replace(/"/g, '""') + '"';
  }

  function sqlNumber(value) {
    if (typeof value !== "number" || !isFinite(value)) {
      throw new Error("Expected a finite number, got " + value);
    }
    return String(value);
  }

  function sqlLiteral(value) {
    if (value === null || value === undefined) {
      return "null";
    }
    if (typeof value === "number") {
      return sqlNumber(value);
    }
    if (typeof value === "boolean") {
      return value ? "1" : "0";
    }
    return "'" + String(value).replace(/'/g, "''") + "'";
  }

  // Replace :name parameters in a SQL fragment with literal values, leaving
  // string literals, quoted identifiers and comments alone. The query
  // endpoint binds every parameter as a string, which would change the
  // meaning of numeric filters such as ?age__gt=30 (sent by the server as the
  // number 30) on columns that have no numeric type affinity.
  function inlineParams(sql, params) {
    params = params || {};
    var quotes = { "'": "'", '"': '"', "`": "`", "[": "]" };
    var out = "";
    var i = 0;
    while (i < sql.length) {
      var ch = sql[i];
      var end;
      if (quotes[ch]) {
        var close = quotes[ch];
        end = i + 1;
        while (end < sql.length) {
          if (sql[end] === close) {
            // A doubled quote character is an escaped quote, except in [...]
            if (close !== "]" && sql[end + 1] === close) {
              end += 2;
              continue;
            }
            break;
          }
          end += 1;
        }
        out += sql.slice(i, end + 1);
        i = end + 1;
      } else if (ch === "-" && sql[i + 1] === "-") {
        end = sql.indexOf("\n", i);
        end = end === -1 ? sql.length : end;
        out += sql.slice(i, end);
        i = end;
      } else if (ch === "/" && sql[i + 1] === "*") {
        end = sql.indexOf("*/", i + 2);
        end = end === -1 ? sql.length : end + 2;
        out += sql.slice(i, end);
        i = end;
      } else if (ch === ":" && /[A-Za-z0-9_$]/.test(sql[i + 1] || "")) {
        var name = /^[A-Za-z0-9_$]+/.exec(sql.slice(i + 1))[0];
        if (!Object.prototype.hasOwnProperty.call(params, name)) {
          throw new Error(`No value for parameter :${name} in the filter`);
        }
        out += sqlLiteral(params[name]);
        i += name.length + 1;
      } else {
        out += ch;
        i += 1;
      }
    }
    return out;
  }

  // filterWhere is a where clause with its parameters already inlined.
  //
  // The queries use common table expressions with short readable names such
  // as "vals" and "stats". SQLite resolves a table name to a CTE of the same
  // name anywhere in the statement, so a table called "stats" (or a filter
  // that mentions one) would hit a "circular reference" error. Any name that
  // appears in the table name or the filter gets a numeric suffix instead.
  function sourceSql(table, column, filterWhere) {
    var t = quoteIdentifier(table);
    var source = {
      from: t,
      ctes: [],
      reserved: (String(table) + " " + (filterWhere || "")).toLowerCase(),
    };
    if (filterWhere) {
      var name = cteName(source, FILTERED_CTE);
      source.from = name;
      source.ctes = [
        `${name} as (
  select ${quoteIdentifier(column)} from ${t}
  where ${filterWhere}
)`,
      ];
    }
    return source;
  }

  // The rows returned by an arbitrary SQL query, wrapped in a CTE with an
  // explicit column list. That works for any read-only query (including ones
  // with their own WITH clause, ORDER BY or LIMIT) and gives every result
  // column a unique name: the server de-duplicates names like two "id"
  // columns from a join. The query's parameters are inlined as literals.
  function querySourceSql(sql, params, columnNames) {
    var body = inlineParams(String(sql).replace(/[\s;]+$/, ""), params);
    var source = { from: null, ctes: [], reserved: body.toLowerCase() };
    var name = cteName(source, QUERY_CTE);
    source.from = name;
    source.ctes = [
      `${name}(${columnNames.map(quoteIdentifier).join(", ")}) as (
${body}
)`,
    ];
    return source;
  }

  function cteName(source, base) {
    var name = base;
    var suffix = 2;
    while (mentionsIdentifier(source.reserved, name)) {
      name = `${base}_${suffix}`;
      suffix += 1;
    }
    return name;
  }

  // True if name appears as a whole word in sql (already lowercased).
  function mentionsIdentifier(sql, name) {
    var start = sql.indexOf(name);
    while (start !== -1) {
      var before = sql[start - 1] || " ";
      var after = sql[start + name.length] || " ";
      if (!/[a-z0-9_$]/.test(before) && !/[a-z0-9_$]/.test(after)) {
        return true;
      }
      start = sql.indexOf(name, start + 1);
    }
    return false;
  }

  function cteNames(source, bases) {
    var names = {};
    bases.forEach((base) => {
      names[base] = cteName(source, base);
    });
    return names;
  }

  function withSql(source, ctes) {
    var all = source.ctes.concat(ctes || []);
    return all.length ? `with ${all.join(",\n")}\n` : "";
  }

  function isNumericSql(column) {
    return `typeof(${column}) in ('integer', 'real')`;
  }

  // One pass over the rows: row counts, null counts, the storage classes
  // actually used by values in this column and the numeric range.
  function overviewSql(source, column) {
    var c = quoteIdentifier(column);
    return `${withSql(source)}select
  count(*) as total_rows,
  count(${c}) as not_null,
  count(distinct ${c}) as distinct_values,
  coalesce(sum(typeof(${c}) = 'integer'), 0) as integer_values,
  coalesce(sum(typeof(${c}) = 'real'), 0) as real_values,
  coalesce(sum(typeof(${c}) = 'text'), 0) as text_values,
  coalesce(sum(typeof(${c}) = 'blob'), 0) as blob_values,
  min(case when ${isNumericSql(c)} then ${c} end) as min_number,
  max(case when ${isNumericSql(c)} then ${c} end) as max_number
from ${source.from}`;
  }

  // Mean, sum and sample standard deviation. The variance is computed in two
  // passes (against the mean) which is more numerically stable than
  // avg(x * x) - avg(x) * avg(x). total() is used instead of sum() because it
  // returns a float and cannot overflow.
  function numericSummarySql(source, column) {
    var c = quoteIdentifier(column);
    var n = cteNames(source, ["vals", "stats"]);
    return `${withSql(source, [
      `${n.vals} as (
  select ${c} as v from ${source.from} where ${isNumericSql(c)}
)`,
      `${n.stats} as (
  select avg(v) as mean from ${n.vals}
)`,
    ])}select
  count(*) as count,
  avg(v) as mean,
  total(v) as sum,
  total((v - mean) * (v - mean)) / nullif(count(*) - 1, 0) as variance,
  coalesce(sum(v = 0), 0) as zeros,
  coalesce(sum(v < 0), 0) as negative
from ${n.vals}, ${n.stats}`;
  }

  // Percentiles using linear interpolation between the two closest ranks,
  // the same as numpy.percentile() and PERCENTILE_CONT. Each value is ranked
  // with the row_number() window function, then the two ranks either side of
  // each requested position are picked out and interpolated.
  function percentilesSql(source, valueExpression, where) {
    var values = PERCENTILES.map((p) => `(${sqlNumber(p)})`).join(", ");
    var n = cteNames(source, [
      "vals",
      "ranked",
      "percentiles",
      "positions",
      "picked",
    ]);
    return `${withSql(source, [
      `${n.vals} as (
  select ${valueExpression} as v from ${source.from} where ${where}
)`,
      `${n.ranked} as (
  select v, row_number() over (order by v) - 1 as idx from ${n.vals}
)`,
      `${n.percentiles}(p) as (
  values ${values}
)`,
      `${n.positions} as (
  select p, (n - 1) * p as pos, cast((n - 1) * p as integer) as lo_idx
  from ${n.percentiles}, (select count(*) as n from ${n.vals})
)`,
      `${n.picked} as (
  select idx, v from ${n.ranked}
  where idx in (select lo_idx from ${n.positions} union select lo_idx + 1 from ${n.positions})
)`,
    ])}select
  p as percentile,
  min(v) + (pos - lo_idx) * (max(v) - min(v)) as value
from ${n.positions}
join ${n.picked} on ${n.picked}.idx in (lo_idx, lo_idx + 1)
group by p
order by p`;
  }

  // Count values in equal-width buckets starting at bucketStart. The query
  // groups by position (1) rather than by the "bucket" alias, because SQLite
  // would resolve "bucket" to the column if the column is called bucket.
  function histogramSql(source, column, bucketStart, bucketWidth) {
    var c = quoteIdentifier(column);
    return `${withSql(source)}select
  cast((${c} - ${sqlNumber(bucketStart)}) / ${sqlNumber(bucketWidth)} as integer) as bucket,
  count(*) as count
from ${source.from}
where ${isNumericSql(c)}
group by 1
order by 1`;
  }

  // Tukey's fences: values more than 1.5 times the interquartile range below
  // the first quartile or above the third quartile.
  function outliersSql(source, column, low, high) {
    var c = quoteIdentifier(column);
    return `${withSql(source)}select
  coalesce(sum(${c} < ${sqlNumber(low)}), 0) as below,
  coalesce(sum(${c} > ${sqlNumber(high)}), 0) as above
from ${source.from}
where ${isNumericSql(c)}`;
  }

  function textSummarySql(source, column) {
    var c = quoteIdentifier(column);
    var n = cteNames(source, ["vals"]);
    return `${withSql(source, [
      `${n.vals} as (
  select ${c} as v, length(${c}) as len from ${source.from} where typeof(${c}) = 'text'
)`,
    ])}select
  count(*) as count,
  min(len) as min_length,
  max(len) as max_length,
  avg(len) as mean_length,
  coalesce(sum(trim(v, ${WHITESPACE}) = ''), 0) as blank,
  coalesce(sum(trim(v, ${WHITESPACE}) != '' and v != trim(v, ${WHITESPACE})), 0) as padded,
  coalesce(sum(instr(v, char(10)) > 0), 0) as multiline,
  coalesce(sum(trim(v) != '' and cast(v as numeric) = v), 0) as numeric_like,
  coalesce(sum(v glob ${ISO_DATE_GLOB}), 0) as date_like,
  substr(min(v), 1, ${MAX_DISPLAY_LENGTH}) as first_value,
  substr(max(v), 1, ${MAX_DISPLAY_LENGTH}) as last_value,
  min(case when v glob ${ISO_DATE_GLOB} then v end) as first_date,
  max(case when v glob ${ISO_DATE_GLOB} then v end) as last_date
from ${n.vals}`;
  }

  // Group ISO-8601 dates by year, month or decade using a prefix of the
  // string: 4 = year, 7 = month, 3 = decade. Grouped by position, as above.
  function dateHistogramSql(source, column, prefixLength) {
    var c = quoteIdentifier(column);
    var bucket =
      prefixLength === 3
        ? `substr(${c}, 1, 3) || '0s'`
        : `substr(${c}, 1, ${sqlNumber(prefixLength)})`;
    return `${withSql(source)}select
  ${bucket} as bucket,
  count(*) as count
from ${source.from}
where typeof(${c}) = 'text'
  and ${c} glob ${ISO_DATE_GLOB}
group by 1
order by 1`;
  }

  // The most common values. Long strings are truncated for display and blobs
  // are reported by size only, but grouping uses the full value.
  function topValuesSql(source, column, limit) {
    var c = quoteIdentifier(column);
    return `${withSql(source)}select
  case
    when typeof(${c}) = 'blob' then null
    when typeof(${c}) = 'text' and length(${c}) > ${MAX_DISPLAY_LENGTH}
      then substr(${c}, 1, ${MAX_DISPLAY_LENGTH})
    else ${c}
  end as value,
  typeof(${c}) as type,
  length(${c}) as length,
  count(*) as count
from ${source.from}
where ${c} is not null
group by ${c}
order by count desc, ${c}
limit ${sqlNumber(limit)}`;
  }

  // Pick a "nice" bucket width (1, 2 or 5 times a power of ten) that splits
  // the range into roughly targetBins buckets. Integer columns never get
  // fractional buckets.
  function histogramBuckets(min, max, allIntegers, targetBins) {
    targetBins = targetBins || HISTOGRAM_TARGET_BINS;
    var range = max - min;
    var width;
    if (range === 0) {
      width = 1;
    } else {
      var raw = range / targetBins;
      var magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
      var residual = raw / magnitude;
      var nice = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
      width = cleanFloat(nice * magnitude);
    }
    if (allIntegers) {
      width = Math.max(1, Math.round(width));
    }
    var start = cleanFloat(Math.floor(min / width) * width);
    var bins = Math.floor(cleanFloat((max - start) / width)) + 1;
    return { start: start, width: width, bins: bins };
  }

  function cleanFloat(value) {
    return Number(value.toPrecision(12));
  }

  function datePrefixLength(first, last) {
    var firstYear = parseInt(first.slice(0, 4), 10);
    var lastYear = parseInt(last.slice(0, 4), 10);
    if (lastYear - firstYear < 2) {
      return 7;
    }
    if (lastYear - firstYear > 60) {
      return 3;
    }
    return 4;
  }

  // Add zero-count buckets for any years, months or decades between the first
  // and last bucket that had no values, so gaps show up in the chart.
  function fillDateBuckets(rows, prefixLength) {
    var counts = new Map(rows.map((row) => [row.bucket, row.count]));
    var first = rows[0].bucket;
    var last = rows[rows.length - 1].bucket;
    var labels = [];
    var year = parseInt(first.slice(0, 4), 10);
    var month = prefixLength === 7 ? parseInt(first.slice(5, 7), 10) : 1;
    var step = prefixLength === 3 ? 10 : 1;
    if (
      !/^\d{4}/.test(first) ||
      !/^\d{4}/.test(last) ||
      (prefixLength === 7 && !month)
    ) {
      return rows.map((row) => ({ label: row.bucket, count: row.count }));
    }
    // Stop after 1,000 buckets in case of unexpected input.
    while (labels.length < 1000) {
      var label =
        prefixLength === 7
          ? `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`
          : prefixLength === 3
            ? `${String(year).slice(0, 3)}0s`
            : String(year).padStart(4, "0");
      labels.push(label);
      if (label >= last) {
        break;
      }
      if (prefixLength === 7) {
        month += 1;
        if (month > 12) {
          month = 1;
          year += 1;
        }
      } else {
        year += step;
      }
    }
    return labels.map((label) => ({
      label: label,
      count: counts.get(label) || 0,
    }));
  }

  // Running queries ========================================================

  // Table pages provide _datasetteTableData, query pages _datasetteQueryData.
  // Either way executeSql is only there if the actor can execute SQL.
  function getConfig() {
    var data = window._datasetteTableData || window._datasetteQueryData;
    if (!data || !data.executeSql) {
      return null;
    }
    if (!data.table && !data.executeSql.query) {
      return null;
    }
    return data;
  }

  function isAvailable() {
    return !!(getConfig() && window.HTMLDialogElement && window.fetch);
  }

  // At most MAX_CONCURRENT_QUERIES statistics queries run at once, so
  // clicking through columns on a large table can't tie up all of
  // Datasette's SQL threads. Queries still waiting when the dialog moves on
  // (another column, the other set of rows, or closed) are dropped without
  // being sent. Queries already sent are left to finish rather than aborted,
  // because SQLite would keep running them anyway: they hold their slot until
  // the server responds, which keeps the load bounded, and their results are
  // ignored.
  var MAX_CONCURRENT_QUERIES = 2;
  var activeQueries = 0;
  var waitingQueries = [];

  function cancelledError() {
    return new DOMException("Query cancelled", "AbortError");
  }

  function acquireQuerySlot(signal) {
    if (signal.aborted) {
      return Promise.reject(cancelledError());
    }
    if (activeQueries < MAX_CONCURRENT_QUERIES) {
      activeQueries += 1;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      var waiter = {
        start() {
          signal.removeEventListener("abort", cancel);
          resolve();
        },
      };
      function cancel() {
        waitingQueries.splice(waitingQueries.indexOf(waiter), 1);
        reject(cancelledError());
      }
      signal.addEventListener("abort", cancel, { once: true });
      waitingQueries.push(waiter);
    });
  }

  // Hand the slot straight to the next waiting query, if there is one.
  function releaseQuerySlot() {
    var next = waitingQueries.shift();
    if (next) {
      next.start();
    } else {
      activeQueries -= 1;
    }
  }

  async function runQuery(sql, signal) {
    await acquireQuerySlot(signal);
    try {
      return await fetchQuery(sql);
    } finally {
      releaseQuerySlot();
    }
  }

  async function fetchQuery(sql) {
    var config = getConfig();
    var params = new URLSearchParams({ sql: sql, _shape: "objects" });
    var response = await fetch(config.executeSql.queryUrl + "?" + params, {
      headers: { Accept: "application/json" },
    });
    var data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok || !data || data.ok === false) {
      var message =
        (data && ((data.errors && data.errors.join(" ")) || data.error)) ||
        `Query failed (HTTP ${response.status})`;
      throw new Error(message);
    }
    return data.rows;
  }

  function editorUrl(sql) {
    var config = getConfig();
    return config.executeSql.editorUrl + "?" + new URLSearchParams({ sql });
  }

  // Formatting =============================================================

  function formatNumber(value) {
    if (value === null || value === undefined) {
      return "–";
    }
    if (typeof value !== "number") {
      return String(value);
    }
    if (Number.isInteger(value)) {
      return value.toLocaleString();
    }
    var abs = Math.abs(value);
    if (abs !== 0 && (abs < 0.0001 || abs >= 1e15)) {
      return value.toExponential(3);
    }
    return value.toLocaleString(undefined, {
      maximumFractionDigits: abs < 1 ? 6 : abs < 100 ? 4 : abs < 1e6 ? 2 : 0,
    });
  }

  function formatLength(value) {
    if (value === null || value === undefined) {
      return "–";
    }
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }

  function formatPercent(count, total) {
    if (!total) {
      return "0%";
    }
    var pct = (count / total) * 100;
    if (pct > 0 && pct < 0.1) {
      return "<0.1%";
    }
    if (pct < 100 && pct > 99.9) {
      return ">99.9%";
    }
    return (
      pct.toLocaleString(undefined, {
        maximumFractionDigits: pct < 10 ? 1 : 0,
      }) + "%"
    );
  }

  function percentileLabel(p) {
    if (p === 0) return "min";
    if (p === 1) return "max";
    if (p === 0.5) return "median";
    return "p" + cleanFloat(p * 100);
  }

  function plural(count, singular, pluralForm) {
    return count === 1 ? singular : pluralForm || singular + "s";
  }

  // DOM helpers ============================================================

  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) {
        return;
      }
      if (key === "className") {
        node.className = value;
      } else if (key === "text") {
        node.textContent = value;
      } else if (key === "style") {
        Object.entries(value).forEach(([property, propertyValue]) => {
          if (property.startsWith("--")) {
            node.style.setProperty(property, propertyValue);
          } else {
            node.style[property] = propertyValue;
          }
        });
      } else {
        node.setAttribute(key, value === true ? "" : value);
      }
    });
    (children || []).forEach((child) => {
      if (child === null || child === undefined || child === false) {
        return;
      }
      node.appendChild(
        typeof child === "string" ? document.createTextNode(child) : child,
      );
    });
    return node;
  }

  function statTile(label, value, detail, key) {
    return h("div", { className: "column-stats-tile", "data-stat": key }, [
      h("dt", { text: label }),
      h("dd", {}, [
        h("span", { className: "column-stats-tile-value", text: value }),
        detail
          ? h("span", { className: "column-stats-tile-detail", text: detail })
          : null,
      ]),
    ]);
  }

  // Show empty and whitespace-only strings as a visible placeholder.
  function textValueNode(value) {
    if (value.trim() === "") {
      return h("span", {
        className: "column-stats-value-special",
        text: value === "" ? "<empty string>" : "<whitespace>",
      });
    }
    return null;
  }

  function rangeValue(value) {
    return h("dd", {}, [
      (value !== null && textValueNode(value)) || String(value ?? "–"),
    ]);
  }

  function statGrid(tiles, columns) {
    return h(
      "dl",
      {
        className: "column-stats-tiles",
        style: {
          "--column-stats-tile-columns": String(columns || tiles.length),
        },
      },
      tiles,
    );
  }

  function section(key, title, subtitle) {
    var body = h("div", { className: "column-stats-section-body" }, [
      h("p", { className: "column-stats-loading", text: "Loading…" }),
    ]);
    var node = h(
      "section",
      { className: "column-stats-section", "data-section": key },
      [
        h("h3", {}, [
          title,
          subtitle
            ? h("span", { className: "column-stats-subtitle", text: subtitle })
            : null,
        ]),
        body,
      ],
    );
    return { node: node, body: body };
  }

  function clearLoading(body) {
    body
      .querySelectorAll(":scope > .column-stats-loading")
      .forEach((node) => node.remove());
  }

  function showError(body, error) {
    clearLoading(body);
    body.appendChild(
      h("p", {
        className: "column-stats-error",
        role: "alert",
        text: error.message || String(error),
      }),
    );
  }

  // Charts =================================================================

  // A bar chart of {label, count} buckets. Hovering or focusing a bar shows
  // its label and count in the caption underneath.
  function barChart(buckets, options) {
    var maxCount = Math.max.apply(
      null,
      buckets.map((bucket) => bucket.count),
    );
    var total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
    var caption = h("p", { className: "column-stats-chart-caption" });
    var defaultCaption = options.caption || "";
    caption.textContent = defaultCaption;
    var bars = buckets.map((bucket) => {
      var description = `${bucket.label}: ${bucket.count.toLocaleString()} ${plural(
        bucket.count,
        "value",
      )} (${formatPercent(bucket.count, total)})`;
      var bar = h(
        "div",
        {
          className: "column-stats-bar",
          title: description,
          "data-count": bucket.count,
        },
        [
          h("span", {
            className: "column-stats-bar-fill",
            style: {
              height: maxCount
                ? Math.max(
                    (bucket.count / maxCount) * 100,
                    bucket.count ? 2 : 0,
                  ) + "%"
                : "0",
            },
          }),
        ],
      );
      bar.addEventListener("mouseenter", () => {
        caption.textContent = description;
      });
      bar.addEventListener("mouseleave", () => {
        caption.textContent = defaultCaption;
      });
      return bar;
    });
    var chart = h(
      "div",
      {
        className: "column-stats-chart",
        role: "img",
        "aria-label": options.ariaLabel,
      },
      bars,
    );
    var axis = h(
      "div",
      { className: "column-stats-axis", "aria-hidden": "true" },
      [
        h("span", { text: options.startLabel }),
        h("span", { text: options.endLabel }),
      ],
    );
    return h("figure", { className: "column-stats-figure" }, [
      chart,
      axis,
      caption,
    ]);
  }

  function percentileList(rows, transform) {
    return h(
      "dl",
      { className: "column-stats-percentiles" },
      rows
        .filter((row) => row.percentile > 0 && row.percentile < 1)
        .map((row) =>
          h(
            "div",
            {
              className: "column-stats-percentile",
              "data-percentile": percentileLabel(row.percentile),
            },
            [
              h("dt", { text: percentileLabel(row.percentile) }),
              h("dd", { text: transform(row.value) }),
            ],
          ),
        ),
    );
  }

  // A horizontal box plot: whiskers from p5 to p95, box from p25 to p75 and
  // a line at the median, drawn on the same scale as the histogram above it.
  function boxPlot(byPercentile, scaleMin, scaleMax) {
    var min = scaleMin;
    var range = scaleMax - scaleMin;
    if (!range || byPercentile[0] === byPercentile[1]) {
      return null;
    }
    function x(value) {
      return ((value - min) / range) * 100 + "%";
    }
    function width(a, b) {
      return ((b - a) / range) * 100 + "%";
    }
    return h(
      "div",
      {
        className: "column-stats-boxplot",
        role: "img",
        "aria-label": `Box plot: 5th to 95th percentile ${formatNumber(
          byPercentile[0.05],
        )} to ${formatNumber(byPercentile[0.95])}, interquartile range ${formatNumber(
          byPercentile[0.25],
        )} to ${formatNumber(byPercentile[0.75])}, median ${formatNumber(
          byPercentile[0.5],
        )}`,
      },
      [
        h("span", {
          className: "column-stats-boxplot-whisker",
          style: {
            left: x(byPercentile[0.05]),
            width: width(byPercentile[0.05], byPercentile[0.95]),
          },
        }),
        h("span", {
          className: "column-stats-boxplot-box",
          style: {
            left: x(byPercentile[0.25]),
            width: width(byPercentile[0.25], byPercentile[0.75]),
          },
        }),
        h("span", {
          className: "column-stats-boxplot-median",
          style: { left: x(byPercentile[0.5]) },
        }),
      ],
    );
  }

  var BOX_PLOT_LEGEND =
    "Box: 25th to 75th percentile · line: median · whiskers: 5th to 95th percentile";

  function typeBreakdown(overview) {
    var types = [
      ["integer", overview.integer_values],
      ["real", overview.real_values],
      ["text", overview.text_values],
      ["blob", overview.blob_values],
    ].filter(([, count]) => count > 0);
    if (types.length < 2) {
      return null;
    }
    var total = types.reduce((sum, [, count]) => sum + count, 0);
    return h("div", { className: "column-stats-types" }, [
      h("p", {
        className: "column-stats-note",
        text: "Values in this column use more than one SQLite storage type:",
      }),
      h(
        "div",
        { className: "column-stats-types-bar", "aria-hidden": "true" },
        types.map(([type, count]) =>
          h("span", {
            className: "column-stats-type-" + type,
            style: { flexGrow: String(count) },
            title: `${type}: ${count.toLocaleString()}`,
          }),
        ),
      ),
      h(
        "ul",
        { className: "column-stats-types-legend" },
        types.map(([type, count]) =>
          h("li", { "data-type": type }, [
            h("span", {
              className: "column-stats-swatch column-stats-type-" + type,
            }),
            `${type} ${count.toLocaleString()} (${formatPercent(count, total)})`,
          ]),
        ),
      ),
    ]);
  }

  // The dialog =============================================================

  var state = null;

  function ensureDialog() {
    if (state) {
      return state;
    }
    var modal = DatasetteModal.create();
    var dialog = modal.dialog;
    dialog.id = DIALOG_ID;
    dialog.className = "column-stats-dialog";
    dialog.setAttribute("aria-labelledby", TITLE_ID);
    dialog.innerHTML = `
      <div class="modal-header">
        <span class="modal-title" id="${TITLE_ID}">Column statistics</span>
        <span class="modal-meta"></span>
      </div>
      <div class="column-stats-scroll">
        <div class="column-stats-columns" hidden>
          <div class="column-stats-tablist" role="tablist" aria-label="Columns"></div>
          <button type="button" class="column-stats-more" aria-expanded="false" hidden></button>
        </div>
        <div class="modal-body column-stats-body" id="column-stats-panel" role="tabpanel"></div>
      </div>
      <div class="modal-footer">
        <span class="footer-info" aria-live="polite"></span>
        <button type="button" class="modal-btn modal-btn-ghost column-stats-close">Close</button>
      </div>
    `;
    document.body.appendChild(modal);
    state = {
      modal: modal,
      dialog: dialog,
      meta: dialog.querySelector(".modal-meta"),
      body: dialog.querySelector(".column-stats-body"),
      columns: dialog.querySelector(".column-stats-columns"),
      tabList: dialog.querySelector(".column-stats-tablist"),
      moreButton: dialog.querySelector(".column-stats-more"),
      tabs: [],
      expanded: false,
      footerInfo: dialog.querySelector(".footer-info"),
      closeButton: dialog.querySelector(".column-stats-close"),
      controller: null,
      options: null,
    };
    buildColumnTabs(state);
    state.closeButton.addEventListener("click", () => {
      modal.requestClose("cancel");
    });
    dialog.addEventListener("close", () => {
      setExpanded(state, false);
      if (state.controller) {
        state.controller.abort();
        state.controller = null;
      }
    });
    return state;
  }

  // Column tabs ============================================================
  // A pill for every column in the table, with its declared type, so the
  // dialog can switch between columns without being closed and reopened. It
  // follows the ARIA tabs pattern: one tab stop, arrow keys move between
  // pills and Enter or Space (or a click) switches column.

  function buildColumnTabs(s) {
    var columns = getConfig().executeSql.columns || [];
    if (!columns.length) {
      return;
    }
    s.columns.hidden = false;
    s.tabs = columns.map((column, index) => {
      var tab = h(
        "button",
        {
          type: "button",
          role: "tab",
          id: `column-stats-tab-${index}`,
          className: "column-stats-column",
          "aria-selected": "false",
          "aria-controls": s.body.id || null,
          tabindex: "-1",
          "data-column": column.name,
          // Shows the full name if a very long one is truncated
          title: column.name,
        },
        [
          h("span", {
            className: "column-stats-column-name",
            text: column.name,
          }),
          // Keeps the name and type apart when the text is copied or read
          column.type ? " " : null,
          column.type
            ? h("span", {
                className: "column-stats-column-type",
                text: column.type,
              })
            : null,
        ],
      );
      tab.addEventListener("click", () => {
        if (tab.getAttribute("aria-selected") === "true") {
          return;
        }
        open(
          Object.assign({}, s.options, {
            column: column.name,
            columnType: column.type,
            focusScopeToggle: false,
          }),
        );
      });
      return tab;
    });
    s.tabs.forEach((tab) => s.tabList.appendChild(tab));
    resetTabStopOnLeave(s);
    // Arrow keys move between the pills on show; the "+N more" button
    // reveals the rest
    s.tabList.addEventListener("keydown", (event) => {
      var visible = s.tabs.filter((tab) => !tab.hidden);
      var index = visible.indexOf(document.activeElement);
      if (index === -1) {
        return;
      }
      var next = {
        ArrowRight: index + 1,
        ArrowDown: index + 1,
        ArrowLeft: index - 1,
        ArrowUp: index - 1,
        Home: 0,
        End: visible.length - 1,
      }[event.key];
      if (next === undefined) {
        return;
      }
      event.preventDefault();
      next = (next + visible.length) % visible.length;
      setTabStop(s, visible[next]);
      visible[next].focus();
    });
    s.moreButton.addEventListener("click", () => {
      setExpanded(s, !s.expanded);
      fitColumnTabs(s);
    });
    // Pills wrap differently at a new width, so fit them again. Only the
    // width matters: fitting them changes the height.
    var lastWidth = 0;
    new ResizeObserver((entries) => {
      var width = entries[entries.length - 1].contentRect.width;
      if (width && width !== lastWidth) {
        lastWidth = width;
        fitColumnTabs(s);
      }
    }).observe(s.columns);
  }

  function setExpanded(s, expanded) {
    s.expanded = expanded;
    s.columns.classList.toggle("is-expanded", expanded);
    s.moreButton.setAttribute("aria-expanded", String(expanded));
  }

  // Collapsed, show as many pills as fit in MAX_TAB_ROWS rows with a
  // "+N more" button at the end of the last one, always including the
  // selected column's pill. Expanded, show every pill and a "Show fewer"
  // button.
  var MAX_TAB_ROWS = 3;

  function fitColumnTabs(s) {
    var tabs = s.tabs;
    var more = s.moreButton;
    if (!tabs.length || !s.dialog.open) {
      return;
    }
    tabs.forEach((tab) => {
      tab.hidden = false;
    });
    more.hidden = true;
    var rowTops = Array.from(new Set(tabs.map((tab) => tab.offsetTop)));
    if (rowTops.length <= MAX_TAB_ROWS) {
      setExpanded(s, false);
      return;
    }
    more.hidden = false;
    if (s.expanded) {
      more.textContent = "Show fewer";
      more.removeAttribute("aria-label");
      return;
    }
    var lastRowTop = rowTops.sort((a, b) => a - b)[MAX_TAB_ROWS - 1];
    var selectedIndex = tabs.findIndex(
      (tab) => tab.getAttribute("aria-selected") === "true",
    );
    // Show the first `count` pills, plus the selected one if it's later
    function showFirst(count) {
      var hiddenCount = 0;
      tabs.forEach((tab, index) => {
        tab.hidden = index >= count && index !== selectedIndex;
        hiddenCount += tab.hidden ? 1 : 0;
      });
      more.textContent = `+${hiddenCount} more`;
      more.setAttribute(
        "aria-label",
        `Show ${hiddenCount} more column${hiddenCount === 1 ? "" : "s"}`,
      );
      return more.offsetTop <= lastRowTop;
    }
    // The most pills that still leave the button on the last row
    var low = 0;
    var high = tabs.length - 1;
    while (low < high) {
      var middle = Math.ceil((low + high) / 2);
      if (showFirst(middle)) {
        low = middle;
      } else {
        high = middle - 1;
      }
    }
    showFirst(low);
  }

  // Tabbing back into the pills lands on the selected column, even after
  // arrowing away from it without switching.
  function resetTabStopOnLeave(s) {
    s.tabList.addEventListener("focusout", (event) => {
      if (s.tabList.contains(event.relatedTarget)) {
        return;
      }
      var selected = s.tabs.find(
        (tab) => tab.getAttribute("aria-selected") === "true",
      );
      if (selected) {
        setTabStop(s, selected);
      }
    });
  }

  function setTabStop(s, activeTab) {
    s.tabs.forEach((tab) => {
      tab.tabIndex = tab === activeTab ? 0 : -1;
    });
  }

  function selectColumnTab(s, column) {
    var selected = null;
    s.tabs.forEach((tab) => {
      var isSelected = tab.dataset.column === column;
      tab.setAttribute("aria-selected", String(isSelected));
      if (isSelected) {
        selected = tab;
      }
    });
    if (selected) {
      setTabStop(s, selected);
      s.body.setAttribute("aria-labelledby", selected.id);
    } else {
      setTabStop(s, s.tabs[0]);
      s.body.removeAttribute("aria-labelledby");
    }
    return selected;
  }

  // Link to the table filtered to a value, on top of the current filters if
  // the statistics were calculated using them.
  function filterUrl(column, value, keepFilters) {
    var config = getConfig();
    var key = column.startsWith("_") ? `${column}__exact` : column;
    var params = new URLSearchParams(keepFilters ? location.search : "");
    params.delete("_next");
    params.set(key, value);
    return config.tableUrl + "?" + params;
  }

  function open(options) {
    if (!isAvailable()) {
      return;
    }
    var config = getConfig();
    var column = options.column;
    var s = ensureDialog();
    if (s.controller) {
      s.controller.abort();
    }
    var controller = new AbortController();
    s.controller = controller;
    s.options = options;

    var activeTab = selectColumnTab(s, column);
    if (activeTab) {
      // The selected pill shows the column and its type
      s.meta.hidden = true;
    } else {
      s.meta.hidden = false;
      s.meta.textContent = options.columnType
        ? `${column} · ${options.columnType}`
        : column;
    }
    s.dialog.dataset.column = column;
    s.body.innerHTML = "";
    s.footerInfo.textContent = "Running queries…";

    var queryLog = [];
    var sqlList = h("ol", { className: "column-stats-sql-list" });
    var sqlDetails = h("details", { className: "column-stats-sql" }, [
      h("summary", { text: "SQL queries" }),
      sqlList,
    ]);
    var started = performance.now();
    var pending = 0;

    function updateFooter() {
      s.dialog.dataset.loading = pending ? "true" : "false";
      var ms = Math.round(performance.now() - started);
      var failed = queryLog.filter((q) => q.error).length;
      s.footerInfo.textContent =
        (pending ? "Running queries… " : "") +
        `${queryLog.length} ${plural(queryLog.length, "query", "queries")}` +
        (failed ? `, ${failed} failed` : "") +
        (pending ? "" : ` in ${ms.toLocaleString()} ms`);
    }

    async function query(title, sql) {
      var entry = { title: title, sql: sql, error: null };
      queryLog.push(entry);
      pending += 1;
      sqlList.appendChild(
        h("li", {}, [
          h("div", { className: "column-stats-sql-title" }, [
            h("span", { text: title }),
            h("a", {
              href: editorUrl(sql),
              target: "_blank",
              rel: "noopener",
              text: "Open in SQL editor",
            }),
          ]),
          h("pre", { text: sql }),
        ]),
      );
      updateFooter();
      try {
        return await runQuery(sql, controller.signal);
      } catch (error) {
        entry.error = error;
        throw error;
      } finally {
        pending -= 1;
        if (!controller.signal.aborted) {
          updateFooter();
        }
      }
    }

    // On a query page the statistics cover the rows the query returns;
    // on a table page the table, or the rows matching its current filters.
    var pageQuery = config.executeSql.query || null;
    var tableFilter = pageQuery ? null : config.executeSql.filter || null;
    var useFilter = !!tableFilter && !options.ignoreFilter;
    var source;
    var filterError = null;
    try {
      source = pageQuery
        ? querySourceSql(
            pageQuery.sql,
            pageQuery.params,
            config.executeSql.columns.map((c) => c.name),
          )
        : sourceSql(
            config.table,
            column,
            useFilter
              ? inlineParams(tableFilter.where, tableFilter.params)
              : null,
          );
    } catch (error) {
      filterError = error;
    }

    // Where a "most common value" links to: the table filtered to that
    // value, or for a query the SQL editor with the query narrowed to it.
    function valueUrl(value) {
      if (pageQuery) {
        return editorUrl(
          `${withSql(source)}select * from ${source.from}\nwhere ${quoteIdentifier(
            column,
          )} = ${sqlLiteral(value)}`,
        );
      }
      return filterUrl(column, String(value), useFilter);
    }
    var overviewSection = section("overview", "Overview");
    s.body.appendChild(overviewSection.node);
    // The SQL list stays last, even if the overview query fails, so failed
    // queries can still be opened in the SQL editor.
    s.body.appendChild(sqlDetails);
    function addSection(node) {
      s.body.insertBefore(node, sqlDetails);
    }
    var scopeToggle = null;
    if (tableFilter) {
      overviewSection.body.before(renderScope());
    }
    if (activeTab) {
      // An earlier fit may have hidden it; it can only be focused if shown.
      // fitColumnTabs() below always keeps it.
      activeTab.hidden = false;
    }
    s.modal.show({
      returnFocusTo: options.returnFocusTo,
      initialFocus: options.focusScopeToggle
        ? scopeToggle
        : activeTab || s.closeButton,
    });
    fitColumnTabs(s);

    // Say which rows the statistics cover, with a button to switch between
    // the filtered rows and the whole table.
    function renderScope() {
      var description = tableFilter.description || "";
      scopeToggle = h("button", {
        type: "button",
        className: "column-stats-scope-toggle",
        text: useFilter ? "Use all rows" : "Use filtered rows",
      });
      scopeToggle.addEventListener("click", () => {
        open(
          Object.assign({}, options, {
            ignoreFilter: useFilter,
            focusScopeToggle: true,
          }),
        );
      });
      return h(
        "div",
        {
          className: "column-stats-scope",
          "data-scope": useFilter ? "filtered" : "all",
        },
        [
          h("p", {}, [
            h("strong", {
              text: useFilter ? "Filtered rows" : "All rows",
            }),
            h("span", {
              className: "column-stats-scope-description",
              text: useFilter
                ? description
                  ? ` ${description}`
                  : " matching the current filters"
                : " in the table, ignoring the current filters",
            }),
          ]),
          scopeToggle,
        ],
      );
    }

    function stillCurrent() {
      return s.controller === controller && !controller.signal.aborted;
    }

    // Errors (including timeouts) are shown in the section that needed the
    // query, unless the dialog has since been closed or reopened.
    function track(promise, body) {
      promise.catch((error) => {
        if (stillCurrent() && body) {
          showError(body, error);
        }
      });
    }

    if (filterError) {
      showError(overviewSection.body, filterError);
      updateFooter();
      return;
    }

    track(
      query("Overview", overviewSql(source, column)).then((rows) => {
        if (!stillCurrent()) return;
        var overview = rows[0];
        renderOverview(overviewSection.body, overview);
        var numericCount = overview.integer_values + overview.real_values;
        // Most common values lead for categorical text columns and follow
        // the distribution for numeric or mostly-unique columns.
        var topValuesFirst =
          overview.text_values >= numericCount &&
          overview.distinct_values <= overview.not_null / 2;
        if (overview.not_null && topValuesFirst) {
          renderTopValues(overview);
        }
        if (numericCount) {
          renderNumeric(overview, numericCount);
        }
        if (overview.text_values) {
          renderText(overview);
        }
        if (overview.not_null && !topValuesFirst) {
          renderTopValues(overview);
        }
      }),
      overviewSection.body,
    );

    function renderOverview(body, overview) {
      clearLoading(body);
      var total = overview.total_rows;
      var nulls = total - overview.not_null;
      // The query page shows at most max_returned_rows rows, but the
      // statistics cover every row the query returns
      if (pageQuery && total > pageQuery.shownRows) {
        body.appendChild(
          h("p", {
            className: "column-stats-note column-stats-query-note",
            text: `Statistics cover all ${formatNumber(
              total,
            )} rows returned by the query, not only the ${formatNumber(
              pageQuery.shownRows,
            )} shown on the page.`,
          }),
        );
      }
      body.appendChild(
        statGrid([
          statTile(
            useFilter ? "Matching rows" : "Rows",
            formatNumber(total),
            null,
            "rows",
          ),
          statTile(
            "Not null",
            formatNumber(overview.not_null),
            formatPercent(overview.not_null, total),
            "not_null",
          ),
          statTile(
            "Null",
            formatNumber(nulls),
            formatPercent(nulls, total),
            "null",
          ),
          statTile(
            "Distinct",
            formatNumber(overview.distinct_values),
            overview.not_null
              ? formatPercent(overview.distinct_values, overview.not_null) +
                  " of non-null"
              : null,
            "distinct",
          ),
        ]),
      );
      var breakdown = typeBreakdown(overview);
      if (breakdown) {
        body.appendChild(breakdown);
      }
      if (overview.blob_values) {
        body.appendChild(
          h("p", {
            className: "column-stats-note",
            text: `${formatNumber(overview.blob_values)} binary ${plural(
              overview.blob_values,
              "value",
            )} not included in the numeric or text statistics.`,
          }),
        );
      }
      if (!total) {
        body.appendChild(
          h("p", {
            className: "column-stats-note",
            text: useFilter
              ? "No rows match the current filters."
              : pageQuery
                ? "The query returned no rows."
                : "This table is empty.",
          }),
        );
      }
    }

    function renderNumeric(overview, numericCount) {
      var mixed = numericCount !== overview.not_null;
      var numbers = section(
        "numeric",
        "Numbers",
        mixed
          ? `${formatNumber(numericCount)} numeric ${plural(numericCount, "value")}`
          : null,
      );
      addSection(numbers.node);
      var body = numbers.body;
      var summaryHolder = h("div");
      var distributionHolder = h("div", {
        className: "column-stats-distribution",
      });
      var percentileHolder = h("div");
      var notesHolder = h("div");
      body.append(
        summaryHolder,
        distributionHolder,
        percentileHolder,
        notesHolder,
      );

      var summaryPromise = query(
        "Numeric summary",
        numericSummarySql(source, column),
      );
      var percentilesPromise = query(
        "Percentiles",
        percentilesSql(
          source,
          quoteIdentifier(column),
          isNumericSql(quoteIdentifier(column)),
        ),
      );

      track(
        Promise.all([summaryPromise, percentilesPromise]).then(
          ([summaryRows, percentileRows]) => {
            if (!stillCurrent()) return;
            clearLoading(body);
            var summary = summaryRows[0];
            var byPercentile = {};
            percentileRows.forEach((row) => {
              byPercentile[row.percentile] = row.value;
            });
            var stddev =
              summary.variance === null ? null : Math.sqrt(summary.variance);
            summaryHolder.appendChild(
              statGrid(
                [
                  statTile(
                    "Min",
                    formatNumber(overview.min_number),
                    null,
                    "min",
                  ),
                  statTile(
                    "Max",
                    formatNumber(overview.max_number),
                    null,
                    "max",
                  ),
                  statTile("Mean", formatNumber(summary.mean), null, "mean"),
                  statTile(
                    "Median",
                    formatNumber(byPercentile[0.5]),
                    null,
                    "median",
                  ),
                  statTile("Std dev", formatNumber(stddev), null, "stddev"),
                  statTile("Sum", formatNumber(summary.sum), null, "sum"),
                ],
                3,
              ),
            );
            var plot = boxPlot(
              byPercentile,
              buckets.start,
              buckets.start + buckets.bins * buckets.width,
            );
            if (plot) {
              distributionHolder.appendChild(plot);
              distributionHolder.appendChild(
                h("p", {
                  className: "column-stats-chart-caption",
                  text: BOX_PLOT_LEGEND,
                }),
              );
            }
            percentileHolder.appendChild(h("h4", { text: "Percentiles" }));
            percentileHolder.appendChild(
              percentileList(percentileRows, formatNumber),
            );
            var notes = [];
            if (summary.zeros) {
              notes.push(
                `${formatNumber(summary.zeros)} ${plural(summary.zeros, "zero")}`,
              );
            }
            if (summary.negative) {
              notes.push(`${formatNumber(summary.negative)} negative`);
            }
            if (notes.length) {
              notesHolder.appendChild(
                h("p", {
                  className: "column-stats-note",
                  text: notes.join(" · "),
                }),
              );
            }
            renderOutliers(notesHolder, byPercentile);
          },
        ),
        body,
      );

      // The histogram only needs the range from the overview query, so it
      // runs alongside the summary and percentile queries.
      var allIntegers = overview.real_values === 0;
      var buckets = histogramBuckets(
        overview.min_number,
        overview.max_number,
        allIntegers,
      );
      var histogramHolder = h("div", { className: "column-stats-histogram" });
      distributionHolder.before(histogramHolder);
      track(
        query(
          "Histogram",
          histogramSql(source, column, buckets.start, buckets.width),
        ).then((rows) => {
          if (!stillCurrent()) return;
          var counts = new Array(buckets.bins).fill(0);
          rows.forEach((row) => {
            var index = Math.min(Math.max(row.bucket, 0), buckets.bins - 1);
            counts[index] += row.count;
          });
          var single = allIntegers && buckets.width === 1;
          var chartBuckets = counts.map((count, i) => {
            var lo = cleanFloat(buckets.start + i * buckets.width);
            var hi = cleanFloat(lo + buckets.width);
            return {
              label: single
                ? formatNumber(lo)
                : allIntegers
                  ? `${formatNumber(lo)} – ${formatNumber(hi - 1)}`
                  : `${formatNumber(lo)} – ${formatNumber(hi)}`,
              count: count,
            };
          });
          var end = cleanFloat(buckets.start + buckets.bins * buckets.width);
          histogramHolder.appendChild(
            barChart(chartBuckets, {
              ariaLabel: `Histogram of ${column}: ${buckets.bins} ${plural(
                buckets.bins,
                "bucket",
              )} of width ${formatNumber(buckets.width)} from ${formatNumber(
                buckets.start,
              )}`,
              startLabel: formatNumber(buckets.start),
              endLabel: formatNumber(allIntegers ? end - 1 : end),
              caption: `${buckets.bins} ${plural(
                buckets.bins,
                "bucket",
              )} of width ${formatNumber(buckets.width)}`,
            }),
          );
        }),
        histogramHolder,
      );
    }

    function renderOutliers(holder, byPercentile) {
      var q1 = byPercentile[0.25];
      var q3 = byPercentile[0.75];
      if (q1 === undefined || q3 === undefined || q3 === q1) {
        return;
      }
      var iqr = q3 - q1;
      var low = cleanFloat(q1 - 1.5 * iqr);
      var high = cleanFloat(q3 + 1.5 * iqr);
      var outlierHolder = h("p", {
        className: "column-stats-note column-stats-outliers",
      });
      holder.appendChild(outlierHolder);
      track(
        query("Outliers", outliersSql(source, column, low, high)).then(
          (rows) => {
            if (!stillCurrent()) return;
            var row = rows[0];
            if (!row.below && !row.above) {
              outlierHolder.textContent = `No outliers: every value is within 1.5 × IQR of the quartiles (${formatNumber(
                low,
              )} to ${formatNumber(high)}).`;
              return;
            }
            outlierHolder.textContent = `Outliers (beyond 1.5 × IQR): ${formatNumber(row.below)} below ${formatNumber(
              low,
            )}, ${formatNumber(row.above)} above ${formatNumber(high)}.`;
          },
        ),
        holder,
      );
    }

    function renderText(overview) {
      var mixed = overview.text_values !== overview.not_null;
      var text = section(
        "text",
        "Text",
        mixed
          ? `${formatNumber(overview.text_values)} text ${plural(
              overview.text_values,
              "value",
            )}`
          : null,
      );
      addSection(text.node);
      var body = text.body;
      var c = quoteIdentifier(column);
      var summaryPromise = query(
        "Text summary",
        textSummarySql(source, column),
      );
      var lengthsPromise = query(
        "Length percentiles",
        percentilesSql(source, `length(${c})`, `typeof(${c}) = 'text'`),
      );
      track(
        Promise.all([summaryPromise, lengthsPromise]).then(
          ([summaryRows, lengthRows]) => {
            if (!stillCurrent()) return;
            clearLoading(body);
            var summary = summaryRows[0];
            var median = lengthRows.find((row) => row.percentile === 0.5);
            if (summary.min_length === summary.max_length) {
              body.appendChild(
                h("p", {
                  className: "column-stats-note column-stats-same-length",
                  text: `Every text value is ${formatLength(
                    summary.min_length,
                  )} ${plural(summary.min_length, "character")} long.`,
                }),
              );
            } else {
              body.appendChild(
                statGrid([
                  statTile(
                    "Min length",
                    formatLength(summary.min_length),
                    null,
                    "min_length",
                  ),
                  statTile(
                    "Max length",
                    formatLength(summary.max_length),
                    null,
                    "max_length",
                  ),
                  statTile(
                    "Mean length",
                    formatLength(summary.mean_length),
                    null,
                    "mean_length",
                  ),
                  statTile(
                    "Median length",
                    formatLength(median ? median.value : null),
                    null,
                    "median_length",
                  ),
                ]),
              );
              body.appendChild(h("h4", { text: "Length percentiles" }));
              body.appendChild(percentileList(lengthRows, formatLength));
            }

            // Only patterns that were found get a row; the rest are listed
            // together on one line.
            var patterns = [
              [
                "blank",
                "Blank or whitespace only",
                "blank values",
                summary.blank,
              ],
              [
                "padded",
                "Leading or trailing whitespace",
                "leading or trailing whitespace",
                summary.padded,
              ],
              [
                "multiline",
                "Contains line breaks",
                "line breaks",
                summary.multiline,
              ],
              [
                "numeric_like",
                "Looks like a number",
                "numbers stored as text",
                summary.numeric_like,
              ],
              [
                "date_like",
                "Starts with an ISO date",
                "ISO dates",
                summary.date_like,
              ],
            ];
            var found = patterns.filter((pattern) => pattern[3]);
            var notFound = patterns.filter((pattern) => !pattern[3]);
            body.appendChild(h("h4", { text: "Patterns" }));
            if (found.length) {
              body.appendChild(
                h(
                  "ul",
                  { className: "column-stats-patterns" },
                  found.map(([key, label, , count]) =>
                    h("li", { "data-pattern": key }, [
                      h("span", { text: label }),
                      h("span", {
                        className: "column-stats-pattern-count",
                        text: `${formatNumber(count)} (${formatPercent(
                          count,
                          summary.count,
                        )})`,
                      }),
                    ]),
                  ),
                ),
              );
            }
            if (notFound.length) {
              body.appendChild(
                h("p", {
                  className: "column-stats-note column-stats-patterns-none",
                  text:
                    "No " +
                    notFound.map((pattern) => pattern[2]).join(", ") +
                    ".",
                }),
              );
            }

            var mostlyDates = summary.date_like > summary.count / 2;
            body.appendChild(
              h("dl", { className: "column-stats-range" }, [
                h("div", { "data-range": "first" }, [
                  h("dt", {
                    text: mostlyDates ? "Earliest" : "First (sorted)",
                  }),
                  rangeValue(
                    mostlyDates ? summary.first_date : summary.first_value,
                  ),
                ]),
                h("div", { "data-range": "last" }, [
                  h("dt", { text: mostlyDates ? "Latest" : "Last (sorted)" }),
                  rangeValue(
                    mostlyDates ? summary.last_date : summary.last_value,
                  ),
                ]),
              ]),
            );
            if (mostlyDates) {
              renderDateHistogram(body, summary);
            }
          },
        ),
        body,
      );
    }

    function renderDateHistogram(body, summary) {
      var prefixLength = datePrefixLength(
        summary.first_date,
        summary.last_date,
      );
      var unit = { 3: "decade", 4: "year", 7: "month" }[prefixLength];
      var holder = h("div", { className: "column-stats-histogram" }, [
        h("h4", { text: `Dates by ${unit}` }),
      ]);
      body.appendChild(holder);
      track(
        query(
          `Dates by ${unit}`,
          dateHistogramSql(source, column, prefixLength),
        ).then((rows) => {
          if (!stillCurrent() || !rows.length) return;
          var chartBuckets = fillDateBuckets(rows, prefixLength);
          holder.appendChild(
            barChart(chartBuckets, {
              ariaLabel: `Values of ${column} by ${unit}, from ${rows[0].bucket} to ${
                rows[rows.length - 1].bucket
              }`,
              startLabel: rows[0].bucket,
              endLabel: rows[rows.length - 1].bucket,
              caption: `${rows.length} ${plural(rows.length, unit)} with values`,
            }),
          );
        }),
        holder,
      );
    }

    function renderTopValues(overview) {
      var top = section("top", "Most common values");
      addSection(top.node);
      var body = top.body;
      if (overview.distinct_values === overview.not_null) {
        clearLoading(body);
        body.appendChild(
          h("p", {
            className: "column-stats-note",
            text:
              overview.not_null === 1
                ? "There is only one non-null value."
                : `All ${formatNumber(overview.not_null)} non-null values are unique.`,
          }),
        );
        return;
      }
      track(
        query(
          "Most common values",
          topValuesSql(source, column, TOP_VALUES_LIMIT),
        ).then((rows) => {
          if (!stillCurrent()) return;
          clearLoading(body);
          // Values that appear only once are an arbitrary pick from a tie,
          // unless they are all of the remaining values.
          if (overview.distinct_values > rows.length) {
            rows = rows.filter((row) => row.count > 1);
          }
          var maxCount = rows.length ? rows[0].count : 0;
          var list = h(
            "ol",
            { className: "column-stats-top-values" },
            rows.map((row) => {
              var label;
              var truncated =
                row.type === "text" && row.length > MAX_DISPLAY_LENGTH;
              if (row.type === "blob") {
                label = h("span", {
                  className: "column-stats-value-special",
                  text: `<Binary: ${formatNumber(row.length)} bytes>`,
                });
              } else if (row.type === "text" && textValueNode(row.value)) {
                label = textValueNode(row.value);
              } else if (truncated) {
                label = h("span", { text: row.value + "…" });
              } else {
                label = h("a", {
                  href: valueUrl(row.value),
                  text:
                    typeof row.value === "number"
                      ? formatNumber(row.value)
                      : String(row.value),
                });
              }
              return h("li", {}, [
                h("span", { className: "column-stats-top-value" }, [label]),
                h("span", {
                  className: "column-stats-top-count",
                  text: `${formatNumber(row.count)} · ${formatPercent(
                    row.count,
                    overview.not_null,
                  )}`,
                }),
                h(
                  "span",
                  { className: "column-stats-top-bar", "aria-hidden": "true" },
                  [
                    h("span", {
                      style: { width: (row.count / maxCount) * 100 + "%" },
                    }),
                  ],
                ),
              ]);
            }),
          );
          body.appendChild(list);
          var shown = rows.reduce((sum, row) => sum + row.count, 0);
          var remaining = overview.not_null - shown;
          var otherDistinct = overview.distinct_values - rows.length;
          if (remaining > 0 && otherDistinct > 0) {
            body.appendChild(
              h("p", {
                className: "column-stats-note",
                text: `${formatNumber(remaining)} other ${plural(
                  remaining,
                  "value",
                )} (${formatPercent(remaining, overview.not_null)}) across ${formatNumber(
                  otherDistinct,
                )} more distinct ${plural(otherDistinct, "value")}.`,
              }),
            );
          }
        }),
        body,
      );
    }
  }

  // _column_stats_loader.html defines a stand-in DatasetteColumnStats whose
  // open() imports this file and then calls the real open() set here.
  window.DatasetteColumnStats = Object.assign(
    window.DatasetteColumnStats || {},
    {
      isAvailable: isAvailable,
      open: open,
      sql: {
        source: sourceSql,
        querySource: querySourceSql,
        inlineParams: inlineParams,
        overview: overviewSql,
        numericSummary: numericSummarySql,
        percentiles: percentilesSql,
        histogram: histogramSql,
        outliers: outliersSql,
        textSummary: textSummarySql,
        dateHistogram: dateHistogramSql,
        topValues: topValuesSql,
        quoteIdentifier: quoteIdentifier,
      },
      histogramBuckets: histogramBuckets,
      PERCENTILES: PERCENTILES,
    },
  );
})();
