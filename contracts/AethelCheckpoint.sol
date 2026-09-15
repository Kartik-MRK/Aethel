// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @notice Immutable checkpoints for one named transparency log.
/// @dev Roots commit to history, not to model quality or honest training.
contract AethelCheckpoint {
    struct Checkpoint {
        bytes32 root;
        uint64 blockNumber;
        uint64 previousSize;
    }

    address public owner;
    address public pendingOwner;
    bytes32 public logId;
    uint64 public latestSize;
    mapping(uint64 => Checkpoint) public checkpoints;

    event Published(bytes32 indexed logId, uint64 indexed size, bytes32 root, uint64 previousSize);
    event OwnershipProposed(address indexed nextOwner);
    event OwnershipAccepted(address indexed newOwner);

    constructor(bytes32 id) {
        require(id != bytes32(0), "empty log id");
        owner = msg.sender;
        logId = id;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "owner only");
        _;
    }

    function publish(uint64 size, bytes32 root, uint64 expectedPreviousSize) external onlyOwner {
        require(size > latestSize, "size must increase");
        require(root != bytes32(0), "empty root");
        require(expectedPreviousSize == latestSize, "checkpoint changed");
        checkpoints[size] = Checkpoint(root, uint64(block.number), latestSize);
        latestSize = size;
        emit Published(logId, size, root, expectedPreviousSize);
    }

    function proposeOwner(address nextOwner) external onlyOwner {
        require(nextOwner != address(0), "empty owner");
        pendingOwner = nextOwner;
        emit OwnershipProposed(nextOwner);
    }

    function acceptOwnership() external {
        require(msg.sender == pendingOwner, "pending owner only");
        owner = pendingOwner;
        pendingOwner = address(0);
        emit OwnershipAccepted(owner);
    }
}
