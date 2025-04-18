import warnings
warnings.filterwarnings("ignore")  # Suppress user warnings

import torch



# Model
# WEIGHTS_DIRECTORY = "data/model"

#CSV PATHS
HOURS = 70
INPUTS_PATH = "data/Inputs"
TRAIN_CSV = f"{INPUTS_PATH}/train_{HOURS}h.csv"
TEST_CSV = f"{INPUTS_PATH}/test_{HOURS}h.csv"
# VALIDATION_CSV = f"{INPUTS_PATH}/validation_{HOURS}h.csv"
WAV2VEC_FOLDER = 'D:\Database\Audio\DeepFakeProject\Wav2vecMatrices' # The folder containing the Wav2Vec matrices
DATASET_FOLDER = "/home/hp4ran/DeepFakeProject"
#OPTUNA PARAMETERS
LOAD_TRAINING = True
DATA_AUGMENTATION = True # to use the previous data augmentation script, set to False
EPOCHS = 100
TRIALS = 15
PATIENCE = 4
PARTIAL_TRAINING = 1 # between 0-1 how much of the data to use

DEBUGMODE = False
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Runnig on device:", DEVICE)


# The model parameters
BATCH_SIZE = 16
DROP_OUT = 0.3

# logs path
TRAINING_DATA_PATH = 'data/results/'  # Directory for saving training results

#a file for dynamic loading ?
