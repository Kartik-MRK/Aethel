const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const solc = require('solc');

const source = fs.readFileSync(path.join(__dirname, 'AethelCheckpoint.sol'), 'utf8');
const input = {
  language: 'Solidity',
  sources: {'AethelCheckpoint.sol': {content: source}},
  settings: {
    optimizer: {enabled: true, runs: 200},
    evmVersion: 'paris',
    metadata: {bytecodeHash: 'none'},
    outputSelection: {'*': {'*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object']}}
  }
};
const output = JSON.parse(solc.compile(JSON.stringify(input)));
for (const error of output.errors || []) {
  process.stderr.write(error.formattedMessage);
  if (error.severity === 'error') process.exit(1);
}
const c = output.contracts['AethelCheckpoint.sol'].AethelCheckpoint;
const artifact = {
  compiler: solc.version(),
  source_sha256: crypto.createHash('sha256').update(source).digest('hex'),
  settings: input.settings,
  abi: c.abi,
  bytecode: '0x' + c.evm.bytecode.object,
  deployedBytecode: '0x' + c.evm.deployedBytecode.object
};
const directory = path.join(__dirname, '../aethel/provenance/contracts');
fs.mkdirSync(directory, {recursive: true});
fs.writeFileSync(path.join(directory, 'AethelCheckpoint.json'), JSON.stringify(artifact, null, 2) + '\n');
process.stdout.write('Built AethelCheckpoint with ' + artifact.compiler + '\n');
