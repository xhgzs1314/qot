
(function() {
    'use strict';
    const QOT_RULES = [
       
        {
            pattern: /(\/\/[^\n]*)/g,
            className: 'qot-comment'
        },
       
        {
            pattern: /(?:L|U)?"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g,
            className: 'qot-string'
        },
      
        {
            pattern: /\b(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+\.\d+|\d+)\b/g,
            className: 'qot-number'
        },
       
        {
            pattern: /\b(?:true|false)\b/g,
            className: 'qot-boolean'
        },
        
        {
            pattern: /\bnull\b/g,
            className: 'qot-null'
        },
      
        {
            pattern: /\b(?:var|const|func|return|if|else|eif|loop|for|in|stop|require|break|continue)\b/g,
            className: 'qot-keyword'
        },
       
        {
            pattern: /\b(?:number_t|string_t|boolean_t|array_t|any_t)\b/g,
            className: 'qot-type'
        },
       
        {
            pattern: /\b(?:output|cinget|glen|push|remove|pop|get|batch_push|strrep|strcase|fopen|fread|fclose|fwrite|unlink|fnew|fsize|sqrt|pow|round|rand|sleep|set_env|get_env|unset_env|tnow|tstamp|cmd_exec|http_send|dict_get|dict_set|dict_keys|ScLoad|ScExec|ScSweep|window_new|window_title|window_size|window_loop|window_button|window_label|window_entry|window_place|window_bind|window_config|window_visible|webloc_init|webloc_sbp|webloc_run|utf8_enc|utf8_dec|gtypes|strings|ints|floats|bools|lists|parts|beair|max|min|sum|prog_end|getpath)\b/g,
            className: 'qot-builtin'
        },
   
        {
            pattern: /(&<|>&)/g,
            className: 'qot-dict-boundary'
        },
        
        {
            pattern: /(->|##)/g,
            className: 'qot-operator-call'
        },
      
        {
            pattern: /&\(\?\)/g,
            className: 'qot-func-ref'
        },

        {
            pattern: /(?:L|U)?"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g,
            className: 'qot-operator'
        },
     
        {
            pattern: /[(){}\[\];:,]/g,
            className: 'qot-punctuation'
        },
        
        {
            pattern: /\b([a-zA-Z_]\w*)(?=\()/g,
            className: 'qot-function-call'
        }
    ];
    function highlightCode(code) {
        if (!code || typeof code !== 'string') return '';
        let result = code;
        for (const rule of QOT_RULES) {
            result = result.replace(rule.pattern, function(match) {
                if (match.indexOf('<span') === 0 && match.indexOf('</span>') > -1) {
                    return match;
                }
                return '<span class="' + rule.className + '">' + escapeHtml(match) + '</span>';
            });
        }
        return result;
    }
    function escapeHtml(str) {
        return str.replace(/[&<>]/g, function(m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            return m;
        });
    }

    
    function processCodeElement(element) {
        if (element.getAttribute('data-highlighted') === 'true') return;
        var code = '';
        if (element.tagName === 'PRE') {
            var innerCode = element.querySelector('code');
            if (innerCode) {
                code = innerCode.textContent || innerCode.innerText;
                var highlighted = highlightCode(code);
                innerCode.innerHTML = highlighted;
                element.setAttribute('data-highlighted', 'true');
                return;
            } else {
                code = element.textContent || element.innerText;
            }
        } else if (element.tagName === 'CODE') {
            code = element.textContent || element.innerText;
        } else {
            return;
        }
        
        if (code) {
            var highlighted = highlightCode(code);
            element.innerHTML = highlighted;
            element.setAttribute('data-highlighted', 'true');
        }
    }

    
    function findAllCodeBlocks() {
  
        var codeTags = document.querySelectorAll('pre code, code');
        for (var i = 0; i < codeTags.length; i++) {
            processCodeElement(codeTags[i]);
        }
        
    
        var codeBlocks = document.querySelectorAll('.code-block, pre');
        for (var i = 0; i < codeBlocks.length; i++) {
            processCodeElement(codeBlocks[i]);
        }
        
     
        var customBlocks = document.querySelectorAll('.qot-code, .language-qot');
        for (var i = 0; i < customBlocks.length; i++) {
            processCodeElement(customBlocks[i]);
        }
    }

  
    function addStyles() {
        var styleId = 'qot-highlight-styles';
        if (document.getElementById(styleId)) return;
        var style = document.createElement('style');
        style.id = styleId;
        style.textContent = `
          
            .qot-comment {
                color: #6a9955;
                font-style: italic;
            }
            .qot-string {
                color: #ce9178;
            }
            .qot-number {
                color: #b5cea8;
            }
            .qot-boolean {
                color: #569cd6;
                font-weight: 500;
            }
            .qot-null {
                color: #569cd6;
                font-weight: 500;
            }
            .qot-keyword {
                color: #c586c0;
                font-weight: 600;
            }
            .qot-type {
                color: #4ec9b0;
                font-weight: 500;
            }
            .qot-builtin {
                color: #dcdcaa;
            }
            .qot-dict-boundary {
                color: #9cdcfe;
                font-weight: 600;
            }
            .qot-operator-call {
                color: #d4d4d4;
                font-weight: 600;
            }
            .qot-func-ref {
                color: #d7ba7d;
                background: #3a3a3a40;
                border-radius: 3px;
                padding: 0 2px;
            }
            .qot-operator {
                color: #d4d4d4;
            }
            .qot-punctuation {
                color: #808080;
            }
            .qot-function-call {
                color: #dcdcaa;
                font-weight: 500;
            }
            
          
            pre, .code-block, pre code, code {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border-radius: 8px;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 0.85rem;
                line-height: 1.5;
            }
            pre, .code-block {
                padding: 1rem;
                overflow-x: auto;
            }
        `;
        document.head.appendChild(style);
    }


    function init() {
        addStyles();
        findAllCodeBlocks();
        
       
        var observer = new MutationObserver(function(mutations) {
            var shouldUpdate = false;
            for (var i = 0; i < mutations.length; i++) {
                if (mutations[i].addedNodes.length) {
                    shouldUpdate = true;
                    break;
                }
            }
            if (shouldUpdate) {
                findAllCodeBlocks();
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
    
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();