import {
  __commonJS
} from "./chunk-PX6F3LHL.js";

// tauri-ui/node_modules/react/cjs/react-jsx-runtime.production.js
var require_react_jsx_runtime_production = __commonJS({
  "tauri-ui/node_modules/react/cjs/react-jsx-runtime.production.js"(exports) {
    "use strict";
    var REACT_ELEMENT_TYPE = /* @__PURE__ */ Symbol.for("react.transitional.element");
    var REACT_FRAGMENT_TYPE = /* @__PURE__ */ Symbol.for("react.fragment");
    function jsxProd(type, config, maybeKey) {
      var key = null;
      void 0 !== maybeKey && (key = "" + maybeKey);
      void 0 !== config.key && (key = "" + config.key);
      if ("key" in config) {
        maybeKey = {};
        for (var propName in config)
          "key" !== propName && (maybeKey[propName] = config[propName]);
      } else maybeKey = config;
      config = maybeKey.ref;
      return {
        $$typeof: REACT_ELEMENT_TYPE,
        type,
        key,
        ref: void 0 !== config ? config : null,
        props: maybeKey
      };
    }
    exports.Fragment = REACT_FRAGMENT_TYPE;
    exports.jsx = jsxProd;
    exports.jsxs = jsxProd;
  }
});

// tauri-ui/node_modules/react/jsx-runtime.js
var require_jsx_runtime = __commonJS({
  "tauri-ui/node_modules/react/jsx-runtime.js"(exports, module) {
    if (true) {
      module.exports = require_react_jsx_runtime_production();
    } else {
      module.exports = null;
    }
  }
});

export {
  require_jsx_runtime
};
//# sourceMappingURL=chunk-AQ2FGELQ.js.map
