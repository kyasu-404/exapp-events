import vue from 'eslint-plugin-vue'
import ts from 'typescript-eslint'
export default ts.config(
  ...vue.configs['flat/recommended'],
  { files: ['**/*.ts'], languageOptions: { parser: ts.parser } },
  { files: ['**/*.ts', '**/*.vue'], languageOptions: { parserOptions: { parser: ts.parser } }, rules: {
    'vue/multi-word-component-names': 'off', 'vue/max-attributes-per-line': 'off',
    'vue/html-self-closing': 'off', 'vue/singleline-html-element-content-newline': 'off',
    'vue/html-indent': 'off', 'vue/attributes-order': 'off',
  } },
)
