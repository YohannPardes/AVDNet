import csv
from datetime import datetime
import optuna
import os
import numpy as np
from tqdm import tqdm
from Architectures.VGG16 import DeepFakeDetection
from Architectures.VGG16_FeaturesOnly import FeaturesOnly
from data_methods import calculate_metrics, get_dataloader
from constants import *


# Early stopping implementation
class EarlyStopping:
    def __init__(self, patience=5, delta=0):
        self.patience = patience
        self.delta = delta
        self.best_loss = None
        self.counter = 0
        self.early_stop = False

    def __call__(self, val_loss):
        if self.best_loss is None:
            self.best_loss = val_loss
        elif val_loss > self.best_loss - self.delta:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_loss = val_loss
            self.counter = 0


def objective(trial):
    """
    Optuna objective function for hyperparameter tuning using training and validation sets.
    """
    best_trial_loss = float('inf')
    # Hyperparameter search space
    learning_rate = trial.suggest_float("learning_rate", 1e-5, 1e-2, log=True)
    batch_size = trial.suggest_categorical("batch_size", [8, 16, 32])
    dropout = trial.suggest_float("dropout", 0.2, 0.65)
    layers = trial.suggest_categorical("dense_layers", [i for i in range(2, 8)])

    # Print the current trial parameters
    print(f"Current trial parameters: {trial.params}")

    train_loader = get_dataloader(TRAIN_CSV, WAV2VEC_FOLDER, batch_size=batch_size, num_workers=2)
    val_loader = get_dataloader(VALIDATION_CSV, WAV2VEC_FOLDER, batch_size=batch_size, num_workers=2)

    # Normalize Features
    mean = np.mean(train_loader.dataset.Xfeatures, axis=0)
    std = np.std(train_loader.dataset.Xfeatures, axis=0)

    # Model initialization
    model = DeepFakeDetection(
        batch_size=batch_size,
        learning_rate=learning_rate,
        dense_layers= layers,
        mean=mean,
        std=std
    ).to(DEVICE)


    # Apply dynamic dropout to the model
    for name, module in model.named_modules():
        if isinstance(module, torch.nn.Dropout):
            module.p = dropout

    # Loss, optimizer, and scheduler
    criterion = torch.nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    early_stopping = EarlyStopping(patience=PATIENCE)

    for epoch in tqdm(range(EPOCHS)):  # Limited epochs for optimization
        model.train()
        train_loss = 0
        count_train = 0
        for wav2vec_batch, x_features_batch, y_batch in train_loader:
            wav2vec_batch, x_features_batch, y_batch = (
                wav2vec_batch.to(DEVICE),
                x_features_batch.to(DEVICE),
                y_batch.to(DEVICE)
            )
            optimizer.zero_grad()

            y_pred = model(wav2vec_batch, x_features_batch).squeeze()
            y_batch = y_batch.view(-1)
            y_pred = y_pred.squeeze(-1)
            loss = criterion(y_pred, y_batch.float())
            loss.backward()
            optimizer.step()
            train_loss += loss.detach().item()

            count_train += 1

        train_loss = train_loss / count_train  # Calculate average training loss

        # Validation phase
        model.eval()
        val_loss = 0
        all_y_true, all_y_pred = [], []
        with torch.no_grad():
            for wav2vec_batch, x_features_batch, y_batch in val_loader:

                wav2vec_batch, x_features_batch, y_batch = (
                    wav2vec_batch.to(DEVICE),
                    x_features_batch.to(DEVICE),
                    y_batch.to(DEVICE)
                )
                y_pred = model(wav2vec_batch, x_features_batch).squeeze()
                val_loss += criterion(y_pred.squeeze(), y_batch.float()).item()
                all_y_true.extend(y_batch.cpu().numpy())
                all_y_pred.extend(y_pred.squeeze().cpu())

        # Compute validation metrics
        val_loss /= len(val_loader)
        accuracy, recall, f1 = calculate_metrics(np.array(all_y_true), np.array(all_y_pred))
        print(f'\nEpoch {epoch} : Train Loss = {train_loss}, Validation Loss = {val_loss}, Accuracy = {accuracy}, Recall = {recall}, F1 = {f1}')

        if val_loss < best_trial_loss:
            best_trial_loss = val_loss
            # Copy current model’s state_dict
            temp_model_path = f"checkpoints/tmp_model_trial_{trial.number}.pth"
            trial.set_user_attr("best_model_path", temp_model_path)
            torch.save(model, temp_model_path)

        # Early stopping check
        early_stopping(val_loss)
        if early_stopping.early_stop:
            break

    # After finishing the epochs for this trial:
    trial.set_user_attr("best_val_loss", best_trial_loss)

    return best_trial_loss


def evaluate_on_test(model, test_csv, batch_size):
    """
    Evaluate the model on the test set after tuning.
    """

    # Create Test DataLoader
    test_loader = get_dataloader(test_csv, WAV2VEC_FOLDER, batch_size=batch_size, num_workers=2)

    # Testing Loop with DataLoader
    model.eval()
    test_loss = 0
    all_y_true, all_y_pred = [], []
    criterion = torch.nn.BCELoss()

    with torch.no_grad():
        for x_paths_batch, x_features_batch, y_batch in test_loader:
            x_features_batch, y_batch = x_features_batch.to(DEVICE), y_batch.to(DEVICE)

            # Choose model type
            if isinstance(model, DeepFakeDetection):
                y_pred = model(x_paths_batch, x_features_batch).squeeze()
            elif isinstance(model, FeaturesOnly):
                y_pred = model(x_features_batch).squeeze()

            # Compute loss
            try:
                test_loss += criterion(y_pred, y_batch).item()
            except ValueError:
                y_pred = y_pred.view_as(y_batch)  # Reshape y_pred to match y_batch
                test_loss += criterion(y_pred, y_batch).item()

            # Store predictions
            all_y_true.extend(y_batch.cpu().numpy())
            all_y_pred.extend(y_pred.cpu().numpy())

    # Average test loss per batch
    test_loss /= len(test_loader)

    # Convert probabilities to binary predictions
    binary_y_pred = (np.array(all_y_pred) > 0.5).astype(int)

    # Compute metrics
    accuracy, recall, f1 = calculate_metrics(np.array(all_y_true), binary_y_pred)

    print(f"Test Loss = {test_loss:.4f}, Accuracy = {accuracy:.4f}, Recall = {recall:.4f}, F1 = {f1:.4f}")
    return accuracy, recall, f1



def save_best_model(study, prefix="DeepFakeModel", extension="pth"):

    best_trial = study.best_trial
    best_model_pth = best_trial.user_attrs["best_model_path"]
    best_val_loss = best_trial.user_attrs["best_val_loss"]
    params = best_trial.params

    # Construct a new filename
    model_filename = (
        f"{prefix}_"
        f"lr={params.get('learning_rate', 0.001)}_"
        f"bs={params.get('batch_size', 32)}_"
        f"drop={params.get('dropout', 0.5):.2f}_"
        f"layers={params.get('dense_layers', 3)}_"
        f"valloss={best_val_loss:.4f}.{extension}"
    )

    # Load the best model's state dict
    saved_model = torch.load(best_model_pth)

    # Save final checkpoint with hyperparams + state_dict
    torch.save(saved_model, model_filename)

    print(f"Best model saved to {model_filename}")
    return model_filename


def load_best_model(model_class, save_path, device="cpu"):
    """
    :param model_class: The class definition for DeepFakeDetection or similar.
    :param save_path: Path to the saved .pth file.
    :param device: "cpu" or "cuda"
    :return: Instantiated model loaded with the best weights.
    """
    checkpoint = torch.load(save_path, map_location=device)
    best_model_path = checkpoint["state_dict"]
    best_params = checkpoint["hyperparams"]

    # Instantiate the model with the best hyperparameters:
    model = model_class(
        batch_size=best_params["batch_size"],
        learning_rate=best_params["learning_rate"],
        dense_layers=best_params["dense_layers"]  # or default
    ).to(device)

    # Load the saved state_dict
    model.load_state_dict(best_model_path)

    return model


def save_all_trials_csv(study, filename_prefix="optuna_results"):
    """
    Save the hyperparameters and metrics of each trial to a CSV file.
    """

    # Generate the timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{filename_prefix}_{timestamp}.csv"

    # Define the header with all the columns we want
    # Adapt column names for your hyperparameters (e.g., dropout vs. drop, etc.)
    header = [
        "trial_number",
        "learning_rate",
        "batch_size",
        "dropout",
        "dense_layers",
        "best_val_loss",
        "best_val_f1",
        "state"
    ]

    # Open the CSV file for writing
    with open(filename, mode="w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)

        for trial in study.trials:  # iterate over all trials
            # If you only want completed trials, do:
            # if trial.state == optuna.trial.TrialState.COMPLETE:

            # Extract hyperparameters from trial.params
            lr = trial.params.get("learning_rate", None)
            bs = trial.params.get("batch_size", None)
            drop = trial.params.get("dropout", None)
            layers = trial.params.get("dense_layers", None)

            # Extract user_attrs from the objective
            val_loss = trial.user_attrs.get("best_val_loss", None)
            val_f1 = trial.user_attrs.get("best_val_f1", None)

            # Write a row to the CSV
            writer.writerow([
                trial.number,     # Unique trial index
                lr,
                bs,
                drop,
                layers,
                val_loss,
                val_f1,
                trial.state.name  # e.g., COMPLETE, PRUNED, FAIL, etc.
            ])

    print(f"All trial results have been saved to '{filename}'.")


def save_best_model_callback(study, trial):
    global best_model_path, best_validation_loss
    this_trial_loss = trial.user_attrs["best_val_loss"]
    this_trial_model_path = trial.user_attrs["best_model_path"]

    if this_trial_loss < best_validation_loss:
        best_validation_loss = this_trial_loss
        best_model_path = this_trial_model_path
        study.set_user_attr("best_model_path", this_trial_model_path)

        print(f"New best model (Trial {trial.number}) saved with val_loss = {best_validation_loss:.4f}")


# Run Optuna optimization
if __name__ == "__main__":
    best_model = None
    best_model_path = None
    best_validation_loss = 1000000

    # Directories and paths
    os.makedirs("checkpoints", exist_ok=True)
    BEST_MODEL_PATH = "checkpoints/best_model.pth"
    BEST_PARAMS_PATH = "checkpoints/best_params.json"
    STUDY_DB_PATH = "sqlite:///checkpoints/optuna_study.db"


    # run the optuna study
    study = optuna.create_study(storage=STUDY_DB_PATH,
                                study_name="speech_classification",
                                direction="minimize",
                                load_if_exists=LOAD_TRAINING)

    study.optimize(objective, n_trials=TRIALS, show_progress_bar=True, callbacks=[save_best_model_callback])

    #save the results
    save_all_trials_csv(study, filename_prefix="data/results/optuna_results")
    path_to_best_model = save_best_model(study)

    # Get the best hyperparameters
    best_params = study.best_params
    print("Best hyperparameters:", best_params)

    # load the best model with the best parameters
    loaded_model = torch.load(study.user_attrs["best_model_path"])
    # loaded_model = torch.load("FeaturesOnly_lr=0.004223516168172755_bs=32_drop=0.45_layers=4_valloss=0.1802.pth")

    # Evaluate on test data
    evaluate_on_test(loaded_model, TEST_CSV, best_params["batch_size"])
    # evaluate_on_test(loaded_model, TEST_CSV, 32)
